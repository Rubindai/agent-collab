"""Settings, diagnostics and terminal run-history administration."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import host_config as CONFIG
import artifacts as ARTIFACTS
import registry as REGISTRY
import runs as RUNS


def run_dir_timestamp(run_dir: Path) -> str:
    try:
        timestamp = run_dir.stat().st_mtime
    except FileNotFoundError:
        timestamp = 0
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


ORPHAN_STARTUP_GRACE_SECONDS = 24 * 60 * 60


def history_candidates(run_root: Path, state_runtime: Any) -> list[dict[str, Any]]:
    loaded_state = state_runtime.load_state(run_root)
    jobs = [job for job in loaded_state.get("jobs", []) if isinstance(job, dict)]
    jobs_by_id = {str(job.get("id")): job for job in jobs if job.get("id")}
    candidates: dict[str, dict[str, Any]] = {}

    if run_root.exists():
        for run_dir in run_root.iterdir():
            if not run_dir.is_dir() or not RUNS.is_direct_run_dir(run_root, run_dir):
                continue
            run_id = run_dir.name
            job = jobs_by_id.get(run_id, {})
            updated_at = str(job.get("updated_at") or job.get("created_at") or run_dir_timestamp(run_dir))
            candidates[run_id] = {
                "run_id": run_id,
                "run_dir": str(run_dir),
                "status": str(job.get("status") or "orphan"),
                "updated_at": updated_at,
                "sort_key": updated_at,
            }

    for job in jobs:
        run_id = str(job.get("id") or "")
        run_dir_text = str(job.get("run_dir") or "")
        if not run_id or not run_dir_text:
            continue
        run_dir = Path(run_dir_text)
        if not run_dir.exists() or run_id in candidates or not RUNS.is_direct_run_dir(run_root, run_dir):
            continue
        updated_at = str(job.get("updated_at") or job.get("created_at") or run_dir_timestamp(run_dir))
        candidates[run_id] = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "status": str(job.get("status") or "unknown"),
            "updated_at": updated_at,
            "sort_key": updated_at,
        }

    return sorted(candidates.values(), key=lambda item: (str(item["sort_key"]), str(item["run_id"])), reverse=True)


def delete_history_candidates(run_root: Path, state_runtime: Any, candidates: list[dict[str, Any]], dry_run: bool) -> list[dict[str, Any]]:
    deleted: list[dict[str, Any]] = []
    for candidate in candidates:
        run_dir = Path(candidate["run_dir"])
        if not RUNS.is_direct_run_dir(run_root, run_dir):
            continue
        deleted.append({key: candidate[key] for key in ("run_id", "run_dir", "status", "updated_at")})
        if not dry_run:
            shutil.rmtree(run_dir)

    if deleted and not dry_run:
        deleted_ids = {item["run_id"] for item in deleted}
        deleted_dirs = {item["run_dir"] for item in deleted}
        with state_runtime.state_lock(run_root):
            loaded_state = state_runtime.load_state(run_root)
            loaded_state["jobs"] = [
                job
                for job in loaded_state.get("jobs", [])
                if isinstance(job, dict)
                and str(job.get("id")) not in deleted_ids
                and str(job.get("run_dir")) not in deleted_dirs
            ]
            state_runtime.save_state(run_root, loaded_state)
    return deleted


def resolve_history_retain(args: argparse.Namespace, default_retained: int = CONFIG.DEFAULT_HISTORY_RETAINED_RUNS) -> int:
    if getattr(args, "all", False):
        return 0
    if getattr(args, "retain", None) is not None:
        return max(int(args.retain), 0)
    return max(int(default_retained), 0)


def build_clear_history_output(args: argparse.Namespace, default_retained: int = CONFIG.DEFAULT_HISTORY_RETAINED_RUNS) -> tuple[int, dict[str, Any] | None]:
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    run_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    state_runtime = CONFIG.STATE
    active_statuses = state_runtime.ACTIVE_STATUSES
    candidates = history_candidates(run_root, state_runtime)
    _, active_guards = REGISTRY.read_workspace_guards(repo_root)
    guarded_run_ids = set(active_guards)
    guarded_run_dirs = {str(Path(str(guard["run_dir"])).resolve()) for guard in active_guards.values()}

    owner_guards: dict[str, Any] = {}

    def owner_guard_holds(candidate: dict[str, Any]) -> bool:
        """Preserve runs another worktree's guard owns, or whose owner is unknown."""

        run_dir = Path(candidate["run_dir"])
        try:
            process_info = ARTIFACTS.read_json_if_exists(run_dir / "peer-process.json")
        except (OSError, ValueError, SystemExit):
            return True
        owner = process_info.get("repo_root") if isinstance(process_info, dict) else None
        if not isinstance(owner, str) or not owner:
            # A startup records ownership only after creating the run directory;
            # keep young orphans until a start could no longer be in progress.
            if candidate["status"] != "orphan":
                return False
            try:
                age = time.time() - run_dir.stat().st_mtime
            except OSError:
                return True
            return age < ORPHAN_STARTUP_GRACE_SECONDS
        if owner not in owner_guards:
            try:
                owner_guards[owner] = REGISTRY.read_workspace_guards(Path(owner))[1]
            except (OSError, ValueError, RuntimeError, SystemExit):
                owner_guards[owner] = "indeterminate"
        guard = owner_guards[owner]
        if guard == "indeterminate":
            return True
        return candidate["run_id"] in guard

    def is_guarded(candidate: dict[str, Any]) -> bool:
        if (candidate["run_id"] in guarded_run_ids
                or str(Path(candidate["run_dir"]).resolve()) in guarded_run_dirs):
            return True
        return owner_guard_holds(candidate)

    if getattr(args, "run", None):
        try:
            target_dir = RUNS.resolve_run_reference(repo_root, args.run, run_root)
            target_id = target_dir.name
        except SystemExit:
            target_id = str(args.run)
        selected = [candidate for candidate in candidates if candidate["run_id"] == target_id]
        retained = [candidate for candidate in candidates if candidate["run_id"] != target_id]
    else:
        terminal = [candidate for candidate in candidates if candidate["status"] not in active_statuses]
        active = [candidate for candidate in candidates if candidate["status"] in active_statuses]
        retain = resolve_history_retain(args, default_retained)
        retained_terminal = terminal[:retain]
        selected = terminal[retain:]
        retained = active + retained_terminal

    guarded_preserved = [candidate for candidate in selected if is_guarded(candidate)]
    if guarded_preserved:
        retained.extend(candidate for candidate in guarded_preserved if candidate not in retained)
    active_preserved = [
        candidate
        for candidate in selected
        if candidate["status"] in active_statuses or is_guarded(candidate)
    ]
    all_active_preserved = [candidate for candidate in candidates if candidate["status"] in active_statuses]
    for candidate in guarded_preserved:
        if candidate not in all_active_preserved:
            all_active_preserved.append(candidate)
    deletable = [
        candidate
        for candidate in selected
        if candidate["status"] not in active_statuses and not is_guarded(candidate)
    ]
    if deletable and not args.dry_run and not args.yes:
        print("clear-history requires --yes for deletion; rerun with --dry-run to preview", file=sys.stderr)
        return 2, None

    deleted = delete_history_candidates(run_root, state_runtime, deletable, args.dry_run)
    output = {
        "action": "clear-history",
        "dry_run": bool(args.dry_run),
        "run_root": str(run_root),
        "deleted": deleted,
        "retained": [{key: candidate[key] for key in ("run_id", "run_dir", "status", "updated_at")} for candidate in retained],
        "active_preserved": [
            {key: candidate[key] for key in ("run_id", "run_dir", "status", "updated_at")}
            for candidate in (active_preserved or all_active_preserved)
        ],
        "missing": [],
        "skipped": [],
    }
    return 0, output


def clear_history(args: argparse.Namespace) -> int:
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    resolved = CONFIG.resolve_settings(repo_root)
    CONFIG.require_valid_settings(resolved)
    settings = resolved["settings"]
    status, output = build_clear_history_output(args, int(settings.get("history_retained_runs", CONFIG.DEFAULT_HISTORY_RETAINED_RUNS)))
    if output is not None:
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return status


def reset_settings(args: argparse.Namespace, repo_root: Path) -> int:
    scopes = ["local", "global"] if args.reset == "all" else [args.reset]
    removed: list[str] = []
    missing: list[str] = []
    for scope in scopes:
        path = CONFIG.settings_path(scope, repo_root)
        if path.exists():
            removed.append(str(path))
            if not args.dry_run:
                path.unlink()
        else:
            missing.append(str(path))
    output = {
        "action": "reset",
        "dry_run": bool(args.dry_run),
        "reset": args.reset,
        "removed": removed,
        "missing": missing,
    }
    return RUNS.emit_result((0, output))


def setup(args: argparse.Namespace) -> int:
    repo_root = CONFIG.canonical_repo_root(args.repo_root or CONFIG.default_repo_root())
    if args.reset:
        return reset_settings(args, repo_root)

    resolved = CONFIG.resolve_settings(repo_root, env={})
    CONFIG.require_valid_settings(resolved)
    settings = dict(resolved["settings"])
    updates = CONFIG.settings_from_args(args)
    settings.update(updates)
    should_clear_history = bool(getattr(args, "clear_history", False))
    if not args.no_input:
        try:
            settings = CONFIG.interactive_setup(settings)
            should_clear_history = should_clear_history or CONFIG.choose_bool("Clear old run history now", False)
        except EOFError:
            print("setup requires interactive stdin; rerun with --no-input for non-interactive use", file=sys.stderr)
            return 2
    if should_clear_history and args.no_input and not args.dry_run and not getattr(args, "yes", False):
        print("setup --clear-history requires --yes for deletion; rerun with --dry-run to preview", file=sys.stderr)
        return 2
    settings = CONFIG.normalize_settings(settings, "setup")
    path = CONFIG.settings_path(args.scope, repo_root)
    if not args.dry_run:
        CONFIG.write_settings_file(path, settings)
    history_cleanup = None
    if should_clear_history:
        cleanup_args = argparse.Namespace(
            repo_root=repo_root,
            run_root=CONFIG.default_run_root(repo_root),
            run=None,
            retain=settings["history_retained_runs"],
            all=False,
            dry_run=args.dry_run,
            yes=getattr(args, "yes", False),
        )
        cleanup_status, history_cleanup = build_clear_history_output(cleanup_args, int(settings["history_retained_runs"]))
        if cleanup_status != 0:
            return cleanup_status
    output = {
        "action": "setup",
        "dry_run": bool(args.dry_run),
        "scope": args.scope,
        "settings_path": str(path),
        "settings": settings,
        "precedence": "environment > local > global > built-in",
    }
    if history_cleanup is not None:
        output["history_cleanup"] = history_cleanup
    if args.print_env:
        output["env"] = CONFIG.settings_to_env(settings)
    return RUNS.emit_result((0, output))


def run_command_capture(command: list[str], timeout: float = 20) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            CONFIG.SAFETY.native_command(command),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "available": True,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except FileNotFoundError as exc:
        return {"available": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {
            "available": True,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": str(exc),
            "timeout": True,
        }


def parse_json_command(command: list[str], timeout: float = 20) -> dict[str, Any]:
    result = run_command_capture(command, timeout)
    data: Any = None
    error: str | None = None
    if result["stdout"]:
        try:
            data = CONFIG.STRICT_JSON.loads(result["stdout"], max_bytes=16 * 1024 * 1024)
        except ValueError as exc:
            error = str(exc)
    return {**result, "json": data, "json_error": error}


def summarize_claude_auth(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    return {
        "logged_in": report.get("loggedIn"),
        "auth_method": report.get("authMethod"),
        "api_provider": report.get("apiProvider"),
        "subscription_type": report.get("subscriptionType"),
    }


def doctor(args: argparse.Namespace) -> int:
    requested_root = (args.repo_root or Path.cwd()).resolve()
    root_error = None
    try:
        repo_root = CONFIG.canonical_repo_root(requested_root)
    except SystemExit as exc:
        repo_root, root_error = requested_root, str(exc)
    run_root = (args.run_root or CONFIG.default_run_root(repo_root)).resolve()
    peer_runtime = CONFIG.PEER
    resolved = CONFIG.resolve_settings(repo_root)
    settings = resolved["settings"]
    cli_paths: dict[str, str | None] = {}
    cli_errors: dict[str, str] = {}
    for name in ("codex", "claude"):
        try:
            cli_paths[name] = CONFIG.SAFETY.resolve_cli(name)
        except FileNotFoundError as exc:
            cli_paths[name] = None
            cli_errors[name] = str(exc)
    codex_path = cli_paths["codex"]
    claude_path = cli_paths["claude"]
    codex_auth = run_command_capture(["codex", "login", "status"], timeout=10) if codex_path else {}
    claude_auth = parse_json_command(["claude", "auth", "status", "--json"], timeout=10) if claude_path else {}
    claude_report = claude_auth.get("json") if isinstance(claude_auth, dict) else None
    settings_errors = CONFIG.settings_error_messages(resolved)
    state_runtime = CONFIG.STATE
    try:
        state_data = state_runtime.load_state(run_root)
    except (OSError, ValueError) as exc:
        state_check = {
            "ok": False,
            "value": str(run_root / "state.json"),
            "error": str(exc),
            "recovery": (
                "Archive this file for evidence and remove or rename it, or choose a fresh "
                "--run-root; legacy state is not migrated or coerced."
            ),
        }
    else:
        state_check = {
            "ok": True,
            "value": str(run_root / "state.json"),
            "jobs": len(state_data["jobs"]),
        }

    schema_errors: list[str] = []
    schema_paths = sorted((CONFIG.resource_root() / "schemas").glob("*.json"))
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
    for schema_path in schema_paths:
        try:
            schema_data = CONFIG.STRICT_JSON.load(schema_path, max_bytes=4 * 1024 * 1024)
            if not isinstance(schema_data, dict):
                raise ValueError("schema root must be an object")
            if jsonschema is not None:
                jsonschema.Draft202012Validator.check_schema(schema_data)
        except Exception as exc:
            schema_errors.append(f"{schema_path.name}: {exc}")

    def cli_version_check(name: str, path: str | None) -> dict[str, Any]:
        minimum = CONFIG.MIN_CLI_VERSIONS[name]
        result = run_command_capture([name, "--version"], timeout=10) if path else {}
        raw = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".strip()
        parsed = CONFIG.parse_cli_version(raw)
        check = {
            "ok": path is not None and parsed is not None and parsed >= minimum,
            "value": {
                "path": path,
                "version": ".".join(str(part) for part in parsed) if parsed else None,
                "minimum": ".".join(str(part) for part in minimum),
            },
        }
        if name in cli_errors:
            check["error"] = cli_errors[name]
        return check

    doctor_workspace_mode = "direct" if settings["safe_mode"] else settings["workspace_mode"]
    isolation_check = (
        CONFIG.ISOLATION.preflight(repo_root)
        if doctor_workspace_mode == "isolated" else
        {"status": "not_required", "backend": None, "details": "direct workspace mode"}
    )
    namespace_check = {"ok": True, "value": "not_required"}
    if sys.platform.startswith("linux"):
        try:
            namespace_check["value"] = CONFIG.SAFETY.pid_namespace()
        except OSError as exc:
            namespace_check = {"ok": False, "value": "/proc/self/ns/pid", "error": str(exc)}

    checks = {
        "repo_root": {"ok": root_error is None, "value": str(repo_root), "error": root_error},
        "run_root": {"ok": run_root.exists() or RUNS.path_is_creatable(run_root), "value": str(run_root)},
        "python": {"ok": sys.version_info >= (3, 11), "value": sys.version.split()[0]},
        "pid_namespace": namespace_check,
        "platform": {
            "ok": CONFIG.is_supported_platform(),
            "value": {"os_name": os.name, "platform": sys.platform, "supported": ["Linux", "macOS", "Windows"]},
        },
        # Git is needed only inside a Git repository.
        "git": {
            "ok": shutil.which("git") is not None or not CONFIG.SNAPSHOT.has_git_metadata(repo_root),
            "value": shutil.which("git"),
        },
        "workspace_isolation": {
            "ok": isolation_check["status"] in {"available", "not_required"},
            "value": {"mode": doctor_workspace_mode, **isolation_check},
        },
        "claude_cli": cli_version_check("claude", claude_path),
        "codex_cli": cli_version_check("codex", codex_path),
        "settings": {
            "ok": not settings_errors,
            "value": {"local": resolved["layers"]["local"]["path"], "global": resolved["layers"]["global"]["path"]},
            "errors": settings_errors,
        },
        "schemas": {
            "ok": bool(schema_paths) and not schema_errors,
            "value": [str(path) for path in schema_paths],
            "errors": schema_errors,
            "meta_validator": "Draft202012Validator" if jsonschema is not None else "strict JSON only",
        },
        "timeout_floor_seconds": {"ok": peer_runtime.MIN_AGENT_TIMEOUT_SECONDS == 60, "value": peer_runtime.MIN_AGENT_TIMEOUT_SECONDS},
        "state_file": state_check,
        "effective_configuration": {
            "ok": not settings_errors,
            "value": {
                "codex": {"model": settings["codex_model"], "effort": settings["codex_effort"]},
                "claude": {"model": settings["claude_model"], "effort": settings["claude_effort"]},
                "online_research": settings["online_research"],
                "workspace_mode": doctor_workspace_mode,
                "uses_built_in_model_effort_defaults": (
                    all(settings[key] == CONFIG.SETTING_DEFAULTS[key] for key in (
                        "codex_model", "codex_effort", "claude_model", "claude_effort"
                    ))
                ),
            },
        },
        "codex_auth": {
            "ok": bool(codex_path) and codex_auth.get("returncode") == 0,
            "value": str(codex_auth.get("stdout") or codex_auth.get("stderr") or "").strip(),
            "returncode": codex_auth.get("returncode") if isinstance(codex_auth, dict) else None,
        },
        "claude_auth": {
            "ok": bool(claude_path) and isinstance(claude_report, dict) and claude_report.get("loggedIn") is True,
            "value": summarize_claude_auth(claude_report),
            "returncode": claude_auth.get("returncode") if isinstance(claude_auth, dict) else None,
        },
        "current_contract": {
            "ok": True,
            "value": {
                "codex": ["exec --strict-config", '-c approval_policy="never"', "--output-schema", "--output-last-message"],
                "claude": [
                    "--permission-mode",
                    "--tools",
                    "--disallowedTools",
                    "--json-schema",
                    "--output-format json",
                    "optional per-run --max-turns",
                ],
            },
        },
    }
    output = {
        "ok": all(item["ok"] for item in checks.values()),
        "checks": checks,
        "settings": settings,
        "setting_sources": resolved["sources"],
        "precedence": "environment > local > global > built-in",
    }
    return RUNS.emit_result((0 if output["ok"] else 1, output))

