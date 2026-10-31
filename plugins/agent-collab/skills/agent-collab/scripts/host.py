#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_config as CONFIG
import artifacts as ARTIFACTS
import registry as REGISTRY
import host_admin as ADMIN
import runs as RUNS
import process_tree as PROCESS_TREE
import lifecycle as LIFECYCLE


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    """Match whole tokens or phrases without triggering inside unrelated words."""

    return any(
        re.search(rf"(?<![\w]){re.escape(needle.strip())}(?![\w])", text) is not None
        for needle in needles
    )


MODE_KEYWORDS = {
    "review": (
        'review', 'audit', 'diff', 'patch', 'pull request', 'pr', 'safe to ship', 'ship',
        'missing test', 'regression', 'security', 'auth',
    ),
    "debug": (
        'bug', 'crash', 'stack trace', 'traceback', 'exception', 'root cause', 'reproduce', 'repro',
        'failing', 'failure', 'hung', 'hanging', 'timeout', 'broken', 'error',
    ),
    "plan": (
        'plan', 'test strategy', 'rollout', 'checklist', 'sequence', 'steps', 'milestone',
        'implementation order', 'execution order', 'rollback',
    ),
    "design": (
        'architecture', 'architect', 'design', 'tradeoff', 'trade-off', 'alternative', 'approach',
        'migration approach', 'moving storage', 'storage provider', 'api shape', 'schema design',
    ),
    "research": (
        'research', 'official doc', 'official documentation', 'official api', 'source-backed',
        'source backed', 'external evidence', 'current docs', 'changed recently', 'platform behavior',
        'dependency behavior',
    ),
}


def classify_mode(target: str, brief: str) -> str:
    text = f"{target}\n{brief}".casefold()
    contexts = {mode: _contains_any(text, words) for mode, words in MODE_KEYWORDS.items()}
    # Planning takes precedence over debugging; review suppresses research.
    for mode in ("plan", "debug", "design"):
        if contexts[mode]:
            return mode
    return "research" if contexts["research"] and not contexts["review"] else "review"


def check_availability(args: argparse.Namespace) -> int:
    availability = CONFIG.AVAILABILITY
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    try:
        availability.bounded_timeout(args.timeout_seconds)
    except ValueError as exc:
        output = availability.result(
            status="unavailable",
            peer=args.peer,
            model=args.model,
            effort=args.effort,
            source="request_contract",
            cli_version=None,
            details=str(exc),
            evidence=str(exc),
        )
    else:
        static = availability.static_failure(args.peer, args.model, args.effort)
        if static is not None:
            output = static
        else:
            try:
                cli_version = CONFIG.require_peer_cli_version(args.peer)
            except SystemExit as exc:
                output = availability.result(
                    status="unknown",
                    peer=args.peer,
                    model=args.model,
                    effort=args.effort,
                    source="cli_preflight",
                    cli_version=None,
                    details=str(exc),
                    evidence=str(exc),
                )
            else:
                output = availability.check(
                    args.peer,
                    args.model,
                    args.effort,
                    cli_version,
                    repo_root,
                    args.timeout_seconds,
                )
    availability.validate_result(output)
    return RUNS.emit_result((availability.EXIT_CODES[output["status"]], output))


def build_start_request(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Any]:
    host = args.host
    peer = "claude" if host == "codex" else "codex"
    brief = CONFIG.read_text_arg(args.brief, args.brief_file)
    if not brief:
        raise SystemExit("brief or brief-file is required")
    mode = args.mode or classify_mode(args.target, brief)
    try:
        run_id = CONFIG.require_safe_run_id(args.run_id or CONFIG.utc_run_id(host, mode))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    max_local_subagents = args.max_local_subagents if args.max_local_subagents is not None else settings["max_local_subagents"]
    local_subagents_allowed = (
        bool(settings["local_subagents_allowed"])
        if args.local_subagents is None
        else bool(args.local_subagents)
    )
    adjudicator_enabled = (
        bool(settings["adjudicator_enabled"])
        if args.adjudicator is None
        else bool(args.adjudicator)
    )
    online_research = settings["online_research"] if args.online_research is None else args.online_research
    peer_model = args.peer_model if args.peer_model is not None else settings[f"{peer}_model"]
    peer_effort = args.peer_effort if args.peer_effort is not None else settings[f"{peer}_effort"]
    if peer == "claude" and CONFIG.is_fable_model(peer_model) and args.peer_model is None:
        raise SystemExit(
            "Fable is explicit-only; request it for this run with "
            "--peer-model claude-fable-5 or --peer-model claude-fable-5-1"
        )
    safe_mode = bool(settings["safe_mode"]) if args.safe_mode is None else bool(args.safe_mode)
    workspace_mode = args.workspace_mode or ("direct" if safe_mode else settings["workspace_mode"])
    if safe_mode and workspace_mode == "isolated":
        raise SystemExit("safe mode uses provider-native permissions; select --workspace-mode direct")
    if workspace_mode == "isolated" and args.edit_allowed:
        raise SystemExit("durable delegated edits require --workspace-mode direct")
    if type(max_local_subagents) is not int or not 0 <= max_local_subagents <= CONFIG.SAFETY.MAX_LOCAL_SUBAGENTS:
        raise SystemExit(f"max_local_subagents must be between 0 and {CONFIG.SAFETY.MAX_LOCAL_SUBAGENTS}")
    agent_timeout = CONFIG.configured_timeout_seconds(
        settings["agent_timeout_seconds"]
        if args.timeout_seconds is None
        else args.timeout_seconds
    )
    claude_max_turns = args.claude_max_turns
    if claude_max_turns is not None and claude_max_turns < 1:
        raise SystemExit("--claude-max-turns must be a positive integer")
    if claude_max_turns is not None and peer != "claude":
        raise SystemExit("--claude-max-turns may be used only when the resolved peer is Claude")
    if peer == "claude":
        policy_failure = CONFIG.SAFETY.claude_helper_policy_failure(peer_model, peer_effort)
        if policy_failure is not None:
            raise SystemExit(CONFIG.STRICT_JSON.dumps(policy_failure))
    request = {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "origin": host,
        "host": host,
        "peer": peer,
        "mode": mode,
        "target": args.target,
        "brief": brief,
        "edit_allowed": bool(args.edit_allowed),
        "run_id": run_id,
        "adjudicator_enabled": adjudicator_enabled,
        "local_subagents_allowed": local_subagents_allowed,
        "max_local_subagents": max_local_subagents,
        "online_research": online_research,
        "safe_mode": safe_mode,
        "workspace_mode": workspace_mode,
        "peer_model": peer_model,
        "peer_effort": peer_effort,
        "peer_timeout_seconds": ARTIFACTS.json_number(agent_timeout),
        "codex_config": list(settings["codex_config"]),
        "claude_tools": settings["claude_tools"],
        "claude_max_turns": claude_max_turns,
        "helper_enforcement": CONFIG.SAFETY.helper_enforcement(
            peer,
            local_subagents_allowed=local_subagents_allowed,
            max_local_subagents=max_local_subagents,
        ),
    }
    return request


def preflight_start(request: dict[str, Any], repo_root: Path) -> str:
    peer = request["peer"]
    peer_model, peer_effort = request["peer_model"], request["peer_effort"]
    safe_mode, workspace_mode = request["safe_mode"], request["workspace_mode"]
    availability_runtime = CONFIG.AVAILABILITY
    # Codex read-only sandboxing uses bubblewrap on Linux. Claude's plan
    # permission mode is provider-native and does not use this backend.
    if safe_mode and peer == "codex":
        sandbox = CONFIG.SAFETY.sandbox_preflight(repo_root, dict(os.environ))
        if sandbox["status"] == "sandbox_unavailable":
            raise SystemExit(
                CONFIG.STRICT_JSON.dumps(
                    {
                        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
                        "status": "sandbox_unavailable",
                        "peer": peer,
                        "safe_mode": True,
                        "backend": sandbox["backend"],
                        "details": sandbox["details"],
                    }
                )
            )
    if workspace_mode == "isolated":
        isolation = CONFIG.ISOLATION.preflight(repo_root)
        if isolation["status"] != "available":
            raise SystemExit(CONFIG.STRICT_JSON.dumps({
                "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
                "status": "sandbox_unavailable",
                "peer": peer,
                "workspace_mode": workspace_mode,
                "backend": isolation["backend"],
                "details": isolation["details"],
            }))
    peer_cli_version = CONFIG.require_peer_cli_version(peer)
    availability_attestation = availability_runtime.check(
        peer,
        peer_model,
        peer_effort,
        peer_cli_version,
        repo_root,
        CONFIG.DEFAULT_AVAILABILITY_TIMEOUT_SECONDS,
    )
    if availability_attestation["status"] != "available":
        raise SystemExit(
            "Agent Collab stopped before run creation or peer launch because the requested "
            f"{peer} pair {peer_model}/{peer_effort} is not attested as available "
            f"(status={availability_attestation['status']}). No peer, helper, host-first-pass, "
            "or adjudicator was started:\n"
            + json.dumps(availability_attestation, ensure_ascii=False, indent=2, sort_keys=True)
        )
    availability_runtime.validate_result(availability_attestation, require_available=True)
    request["availability_attestation"] = availability_attestation
    return peer_cli_version


def peer_process_artifact(request: dict[str, Any], resolved: dict[str, Any], repo_root: Path,
                          run_dir: Path, guard_path: Path, owner: dict[str, Any],
                          peer_identity: dict[str, Any], started_at_epoch: float, peer_cli_version: str) -> dict[str, Any]:
    return {
        "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
        "pid": peer_identity["pid"],
        "pgid": peer_identity["pgid"],
        "process_identity": peer_identity,
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "repo_root": str(repo_root),
        "host": request["host"],
        "peer": request["peer"],
        "adjudicator_enabled": request["adjudicator_enabled"],
        "started_at": ARTIFACTS.utc_timestamp(started_at_epoch),
        "started_at_epoch": started_at_epoch,
        "peer_timeout_seconds": request["peer_timeout_seconds"],
        "peer_cli_version": peer_cli_version,
        "workspace_guard": str(guard_path),
        "owner_token": owner["owner_token"],
        "request_sha256": ARTIFACTS.canonical_artifact_sha256(request),
        "settings": {
            "local": resolved["layers"]["local"]["path"],
            "global": resolved["layers"]["global"]["path"],
        },
        "peer_report": str(run_dir / "peer-report.json"),
        "peer_raw": str(run_dir / "peer.raw.json"),
        "peer_normalization": str(run_dir / "peer-normalization.json"),
        "provider_process": str(run_dir / "provider-process.json"),
        "host_first_pass": str(run_dir / "host-first-pass.json"),
    }


def spawn_peer_wrapper(request_path: Path, repo_root: Path, run_dir: Path,
                       peer_stdout: Any, peer_stderr: Any, startup_gate_read: int) -> Any:
    env = dict(os.environ)
    env["AGENT_COLLAB_RUN_DIR"] = str(run_dir)
    popen_options: dict[str, Any] = {}
    startup_gate_token = CONFIG.SAFETY.pass_gate(startup_gate_read, popen_options)
    return CONFIG.SAFETY.popen_group(
        [
            sys.executable,
            str(CONFIG.runtime_dir() / "peer.py"),
            str(request_path),
            "--repo-root",
            str(repo_root),
            "--raw-output",
            str(run_dir / "peer.raw.json"),
            "--normalization-output",
            str(run_dir / "peer-normalization.json"),
            "--startup-gate-fd",
            str(startup_gate_token),
        ],
        detached=True,
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=peer_stdout,
        stderr=peer_stderr,
        env=env,
        text=True,
        encoding="utf-8",
        **popen_options,
    )


def launch_start(repo_root: Path, run_root: Path, resolved: dict[str, Any], request: dict[str, Any], peer_cli_version: str) -> int:
    run_id, host, peer = request["run_id"], request["host"], request["peer"]
    workspace_mode = request["workspace_mode"]
    adjudicator_enabled = request["adjudicator_enabled"]
    state = CONFIG.STATE
    run_dir = run_root / run_id
    owner = {"run_id": run_id, "repo_root": str(repo_root), "run_dir": str(run_dir),
             "host": host, "owner_token": secrets.token_hex(16)}
    guard_path = REGISTRY.acquire_workspace_guard(repo_root, run_id, host, run_dir, owner_token=owner["owner_token"])
    run_dir_created = False
    peer_stdout = None
    peer_stderr = None
    process = None
    peer_identity = None
    state_job_created = False
    gate = CONFIG.SAFETY.StartupGate()
    try:
        CONFIG.STRICT_JSON.ensure_private_directory(run_dir.parent)
        run_dir.mkdir(exist_ok=False, mode=0o700)
        os.chmod(run_dir, 0o700)
        run_dir_created = True

        request_path = run_dir / "host-request.json"
        CONFIG.STRICT_JSON.write(request_path, request)
        LIFECYCLE.run_snapshot(repo_root, run_dir / "before.snapshot", ignored_paths=[run_dir], workspace_mode=workspace_mode)
        peer_stdout = CONFIG.STRICT_JSON.open_private_text(run_dir / "peer-report.json")
        peer_stderr = CONFIG.STRICT_JSON.open_private_text(run_dir / "peer.stderr.log")
        started_at_epoch = time.time()
        startup_gate_read = gate.open()
        process = spawn_peer_wrapper(request_path, repo_root, run_dir, peer_stdout, peer_stderr, startup_gate_read)
        try:
            peer_identity = CONFIG.SAFETY.process_identity(process.pid)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(
                "peer runtime exited before its exact process identity could be recorded"
            ) from exc
        process_info = peer_process_artifact(request, resolved, repo_root, run_dir, guard_path, owner,
                                             peer_identity, started_at_epoch, peer_cli_version)
        CONFIG.STRICT_JSON.write(run_dir / "peer-process.json", process_info)
        REGISTRY.update_workspace_guard(
            repo_root,
            run_id,
            owner=owner,
            phase="peer_running",
            peer_identity=peer_identity,
        )
        state.upsert_job(
            run_root,
            {
                "id": run_id,
                "run_dir": str(run_dir),
                "repo_root": str(repo_root),
                "host": host,
                "peer": peer,
                "mode": request["mode"],
                "target": request["target"],
                "status": "running",
                "phase": "peer_running",
                "pid": process.pid,
                "adjudicator_enabled": adjudicator_enabled,
                "edit_allowed": request["edit_allowed"],
                "peer_report": str(run_dir / "peer-report.json"),
                "peer_raw": str(run_dir / "peer.raw.json"),
                "peer_normalization": str(run_dir / "peer-normalization.json"),
                "host_first_pass": str(run_dir / "host-first-pass.json"),
                "peer_stderr": str(run_dir / "peer.stderr.log"),
            },
        )
        state_job_created = True
        gate.release()
    except BaseException as startup_error:
        gate.close()
        if peer_stdout is not None:
            peer_stdout.close()
        if peer_stderr is not None:
            peer_stderr.close()
        cleanup = PROCESS_TREE.cleanup_failed_start(process, peer_identity)
        if cleanup.get("quiescent") is not True:
            if run_dir_created:
                try:
                    CONFIG.STRICT_JSON.write(
                        run_dir / "startup-cleanup-failure.json",
                        {
                            "schema_version": CONFIG.ARTIFACT_SCHEMA_VERSION,
                            "run_id": run_id,
                            "cleanup": cleanup,
                            "startup_error": f"{type(startup_error).__name__}: {startup_error}",
                        },
                    )
                except (OSError, ValueError):
                    pass
            raise RuntimeError(
                "peer startup failed and process-group quiescence could not be proven; "
                f"the workspace guard and run artifacts were retained: {cleanup}"
            ) from startup_error
        try:
            if state_job_created:
                state.remove_job(run_root, run_id)
            REGISTRY.release_workspace_guard(repo_root, run_id, owner=owner)
        except (OSError, RuntimeError, ValueError) as rollback_error:
            raise RuntimeError(
                "peer startup process cleanup succeeded but atomic state/guard rollback failed; "
                "run artifacts were retained"
            ) from rollback_error
        if run_dir_created:
            shutil.rmtree(run_dir)
        raise
    finally:
        gate.close()
        if peer_stdout is not None and not peer_stdout.closed:
            peer_stdout.close()
        if peer_stderr is not None and not peer_stderr.closed:
            peer_stderr.close()
    return RUNS.emit_result((0, {**process_info, "host_policy": LIFECYCLE.host_policy(request),
                           "next_action": LIFECYCLE.next_action(run_dir, repo_root, "first_pass")}))


def start(args: argparse.Namespace) -> int:
    CONFIG.require_supported_platform()
    peer_runtime = CONFIG.PEER
    if peer_runtime.nested_invocation_requested(dict(os.environ)):
        raise SystemExit("nested Agent Collab invocation refused by the cross-agent depth guard")
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    run_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    try:
        RUNS.require_run_root_format(run_root)
    except (OSError, ValueError) as exc:
        raise SystemExit(
            "invalid v3 Agent Collab state; refusing to launch a peer: "
            f"{exc}\nUse the original runtime to close or inspect old runs, or select a fresh "
            "--run-root. Existing history is not migrated or rewritten."
        ) from exc
    REGISTRY.require_workspace_guard_available(repo_root)
    resolved = CONFIG.resolve_settings(repo_root)
    CONFIG.require_valid_settings(resolved)
    request = build_start_request(args, resolved["settings"])
    peer_cli_version = preflight_start(request, repo_root)
    peer_runtime.validate_request(request, require_fresh_attestation=True)
    return launch_start(repo_root, run_root, resolved, request, peer_cli_version)


def recover(args: argparse.Namespace) -> int:
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    path = REGISTRY.recover_workspace_guard(repo_root, args.run)
    print(CONFIG.STRICT_JSON.dumps({"status": "recovered", "run_id": args.run, "registry": str(path)}))
    return 0


def status(args: argparse.Namespace) -> int:
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    run_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    state = CONFIG.STATE

    if getattr(args, "active", False):
        if args.run or args.wait or args.all:
            raise SystemExit("status --active cannot be combined with a run, --wait, or --all")
        path, entries = REGISTRY.read_workspace_guards(repo_root)
        print(CONFIG.STRICT_JSON.dumps({"registry": str(path), "runs": list(entries.values())}))
        return 0

    if args.run:
        run_dir = RUNS.resolve_run_reference(repo_root, args.run, run_root)
        if args.wait:
            deadline = time.time() + args.timeout_seconds
            while time.time() < deadline:
                summary = RUNS.summarize_run(run_dir)
                if not summary["peer_alive"]:
                    break
                time.sleep(args.poll_interval_seconds)
        result = RUNS.summarize_run(run_dir)
        job = state.find_job(run_root, result["run_id"])
        if job:
            result["job"] = job
        return RUNS.emit_result((0, result))

    jobs = state.list_jobs(run_root)
    if not args.all:
        jobs = jobs[:8]
    result = {
        "run_root": str(run_root),
        "jobs": jobs,
    }
    return RUNS.emit_result((0, result))


def result(args: argparse.Namespace) -> int:
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    run_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    run_dir = RUNS.resolve_run_reference(repo_root, args.run, run_root)
    if getattr(args, "format", "json") == "markdown":
        if args.review is None:
            raise SystemExit("result --format markdown requires --review DIR from finish")
        with REGISTRY.run_operation_lock(run_dir):
            process_info = ARTIFACTS.load_json(run_dir / "peer-process.json")
            ARTIFACTS.validate_peer_process(process_info, RUNS.require_run_request(run_dir), run_dir)
            documents = LIFECYCLE.review_documents(run_dir)
            review = CONFIG.REVIEW
            review.verify(run_dir, args.review, process_info, documents)
            print(review.markdown(documents))
        return 0
    if getattr(args, "review", None) is not None:
        raise SystemExit("--review requires --format markdown")
    process_info = ARTIFACTS.read_json_if_exists(run_dir / "peer-process.json")
    output = {
        "run": RUNS.summarize_run(run_dir),
        "process": process_info,
        "request": ARTIFACTS.read_json_if_exists(run_dir / "host-request.json"),
        "host_first_pass": ARTIFACTS.read_json_if_exists(run_dir / "host-first-pass.json"),
        "host_synthesis": ARTIFACTS.read_json_if_exists(run_dir / "host-synthesis.json"),
        "peer_normalization": ARTIFACTS.read_json_if_exists(run_dir / "peer-normalization.json"),
        "peer_report": ARTIFACTS.read_json_if_exists(run_dir / "peer-report.json"),
        "claim_matrix": ARTIFACTS.read_json_if_exists(run_dir / "claim-matrix.json"),
        "adjudicator_report": ARTIFACTS.read_json_if_exists(run_dir / "adjudicator-report.json"),
        "host_result": ARTIFACTS.read_json_if_exists(run_dir / "host-result.json"),
        "workspace_mutation": ARTIFACTS.read_json_if_exists(run_dir / "workspace-mutation.json"),
        "cancel_result": ARTIFACTS.read_json_if_exists(run_dir / "cancel-result.json"),
        "cancel_workspace_mutation": ARTIFACTS.read_json_if_exists(run_dir / "cancel-workspace-mutation.json"),
        "peer_stderr_tail": RUNS.stderr_tail(run_dir / "peer.stderr.log"),
    }
    return RUNS.emit_result((0, output))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Agent Collab peer-first host run artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_boolean_override(command, option, help_text):
        command.add_argument(option, action=argparse.BooleanOptionalAction, default=None, help=help_text)

    start_parser = sub.add_parser("start", help="Create a run directory and launch the cross-agent peer.")
    start_parser.add_argument("--host", choices=sorted(CONFIG.ORIGINS), required=True)
    start_parser.add_argument("--mode", choices=sorted(CONFIG.MODES))
    start_parser.add_argument("--target", required=True)
    start_parser.add_argument("--brief")
    start_parser.add_argument("--brief-file", type=Path)
    start_parser.add_argument("--edit-allowed", action="store_true")
    add_boolean_override(start_parser, '--adjudicator', 'Override the host-local advisory adjudicator for this run.')
    start_parser.add_argument("--peer-model", help="Override the resolved peer model for this run.")
    start_parser.add_argument(
        "--peer-effort",
        help="Override the resolved peer effort; live availability validation is authoritative.",
    )
    add_boolean_override(start_parser, '--online-research', 'Override online research for this run.')
    start_parser.add_argument("--max-local-subagents", type=CONFIG.cli_integer)
    add_boolean_override(start_parser, '--local-subagents', 'Override native local helper availability for this run.')
    add_boolean_override(start_parser, '--safe-mode', 'Override the resolved permission posture for this run.')
    start_parser.add_argument(
        "--workspace-mode", choices=["isolated", "direct"],
        help="Select a disposable copy-on-write workspace or direct host access for this run.",
    )
    start_parser.add_argument(
        "--timeout-seconds",
        type=CONFIG.cli_number,
        default=None,
        help="Override the hard finite peer deadline for this run (60-86400 seconds).",
    )
    start_parser.add_argument(
        "--claude-max-turns",
        type=CONFIG.cli_integer,
        help="Explicitly opt into a positive Claude CLI turn cap for this run.",
    )
    start_parser.add_argument("--run-id")
    start_parser.add_argument("--run-root", type=Path)
    start_parser.add_argument("--repo-root", type=Path, help="Workspace directory; Git subdirectories use the whole worktree.")
    start_parser.set_defaults(func=start)

    availability_parser = sub.add_parser(
        "check-availability",
        help="Check one exact peer model/effort pair and print a strict JSON result.",
    )
    availability_parser.add_argument("--peer", choices=sorted(CONFIG.ORIGINS), required=True)
    availability_parser.add_argument("--model", required=True)
    availability_parser.add_argument("--effort", required=True)
    availability_parser.add_argument(
        "--timeout-seconds",
        type=CONFIG.cli_number,
        default=CONFIG.DEFAULT_AVAILABILITY_TIMEOUT_SECONDS,
        help=f"Bound the provider metadata check to at most {CONFIG.MAX_AVAILABILITY_TIMEOUT_SECONDS:g} seconds.",
    )
    availability_parser.add_argument("--repo-root", type=Path)
    availability_parser.set_defaults(func=check_availability)

    setup_parser = sub.add_parser("setup", help="Configure Agent Collab local or global peer defaults.")
    setup_parser.add_argument("--scope", choices=["local", "global"], default="local")
    setup_parser.add_argument("--no-input", action="store_true", help="Do not prompt; write provided flags over current defaults.")
    setup_parser.add_argument("--dry-run", action="store_true", help="Show changes without writing or removing settings files.")
    setup_parser.add_argument("--print-env", action="store_true", help="Include equivalent environment variables in output.")
    setup_parser.add_argument("--reset", choices=sorted(CONFIG.RESET_SCOPES), help="Remove local, global, or all Agent Collab settings.")
    setup_parser.add_argument("--local-subagents-allowed", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument("--max-local-subagents", type=CONFIG.cli_integer)
    setup_parser.add_argument("--adjudicator-enabled", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument("--timeout-seconds", type=CONFIG.cli_number)
    setup_parser.add_argument("--history-retained-runs", type=CONFIG.cli_integer)
    setup_parser.add_argument("--clear-history", action="store_true", help="Clear old run history after setup.")
    setup_parser.add_argument("--yes", action="store_true", help="Confirm setup actions that delete run history.")
    setup_parser.add_argument("--safe-mode", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument("--workspace-mode", choices=["isolated", "direct"])
    setup_parser.add_argument("--codex-model")
    setup_parser.add_argument("--codex-effort")
    setup_parser.add_argument("--online-research", action=argparse.BooleanOptionalAction)
    setup_parser.add_argument(
        "--codex-config",
        action="append",
        metavar="key=value",
        help="Additional Codex config override to pass as `codex exec -c key=value`; repeat for multiple entries.",
    )
    setup_parser.add_argument("--claude-model")
    setup_parser.add_argument("--claude-effort")
    setup_parser.add_argument("--claude-tools")
    setup_parser.add_argument("--repo-root", type=Path)
    setup_parser.set_defaults(func=ADMIN.setup)

    for name, handler, help_text in (
        ("advance", LIFECYCLE.advance, "Resume a run; return the next input and its captured review evidence."),
        ("finish", LIFECYCLE.finish, "Validate peer output and write synthesis support artifacts."),
        ("complete", LIFECYCLE.complete, "Close a synthesized run and release the run guard."),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("run", help="Run ID, unique prefix, or run directory.")
        command.add_argument("--run-root", type=Path)
        command.add_argument("--repo-root", type=Path)
        if name != "complete":
            command.add_argument("--first-pass", type=Path, help="Semantic JSON with summary and claims.")
            command.add_argument("--helpers", type=Path, help="Semantic JSON with a reports array.")
            command.add_argument("--adjudicator", type=Path, help="Semantic JSON advisory report without run metadata.")
            command.add_argument("--timeout-seconds", type=CONFIG.cli_number, default=None)
        if name != "finish":
            command.add_argument("--synthesis", type=Path, help="Edited generated synthesis.json; metadata is filled automatically.")
        command.set_defaults(func=handler)

    status_parser = sub.add_parser("status", help="Print active/recent jobs or one run's artifact status.")
    status_parser.add_argument("run", nargs="?", help="Optional run ID, unique prefix, or run directory.")
    status_parser.add_argument("--active", action="store_true", help="List active worktree registry owners across run roots.")
    status_parser.add_argument("--all", action="store_true", help="List all retained jobs instead of the newest eight.")
    status_parser.add_argument("--wait", action="store_true", help="When inspecting one run, wait while the peer is alive.")
    status_parser.add_argument("--timeout-seconds", type=CONFIG.cli_number, default=2700)
    status_parser.add_argument("--poll-interval-seconds", type=CONFIG.cli_number, default=2)
    status_parser.add_argument("--run-root", type=Path)
    status_parser.add_argument("--repo-root", type=Path)
    status_parser.set_defaults(func=status)

    result_parser = sub.add_parser("result", help="Print complete stored output for a run.")
    result_parser.add_argument("run", help="Explicit run ID or run directory.")
    result_parser.add_argument("--format", choices=["json", "markdown"], default="json")
    result_parser.add_argument("--review", type=Path, help="Evidence revision directory returned by finish.")
    result_parser.add_argument("--run-root", type=Path)
    result_parser.add_argument("--repo-root", type=Path)
    result_parser.set_defaults(func=result)

    clear_history_parser = sub.add_parser("clear-history", help="Delete old Agent Collab run history.")
    clear_history_parser.add_argument("--retain", type=CONFIG.cli_integer, help="Number of newest terminal runs to keep.")
    clear_history_parser.add_argument("--all", action="store_true", help="Delete all terminal runs.")
    clear_history_parser.add_argument("--run", help="Run ID, unique prefix, or run directory to delete.")
    clear_history_parser.add_argument("--dry-run", action="store_true", help="Show deletion candidates without deleting.")
    clear_history_parser.add_argument("--yes", action="store_true", help="Confirm deletion without prompting.")
    clear_history_parser.add_argument("--run-root", type=Path)
    clear_history_parser.add_argument("--repo-root", type=Path)
    clear_history_parser.set_defaults(func=ADMIN.clear_history)

    cancel_parser = sub.add_parser("cancel", help="Cancel an active peer run.")
    cancel_parser.add_argument("run", help="Run ID, unique prefix, or run directory.")
    cancel_parser.add_argument("--reason", help="Optional reason for explicitly cancelling the run.")
    cancel_parser.add_argument("--run-root", type=Path)
    cancel_parser.add_argument("--repo-root", type=Path)
    cancel_parser.set_defaults(func=LIFECYCLE.cancel)

    recover_parser = sub.add_parser("recover", help="Recover one abandoned starting registry entry without deleting its artifacts.")
    recover_parser.add_argument("run", help="Exact active run ID.")
    recover_parser.add_argument("--repo-root", type=Path)
    recover_parser.set_defaults(func=recover)

    doctor_parser = sub.add_parser("doctor", help="Check Agent Collab local runtime prerequisites.")
    doctor_parser.add_argument("--run-root", type=Path)
    doctor_parser.add_argument("--repo-root", type=Path)
    doctor_parser.set_defaults(func=ADMIN.doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    CONFIG.STRICT_JSON.use_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Agent Collab: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
