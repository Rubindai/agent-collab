#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ORIGINS = {"claude", "codex"}
MODES = {
    "review",
    "audit",
    "research",
    "design",
    "plan",
    "plan-critique",
    "debug",
    "migration",
    "test-strategy",
    "verify",
    "implement",
}


def runtime_dir() -> Path:
    return Path(__file__).resolve().parent


def resource_root() -> Path:
    return runtime_dir().parent


def default_repo_root() -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    return Path.cwd().resolve()


def load_peer_runtime() -> Any:
    path = runtime_dir() / "peer.py"
    spec = importlib.util.spec_from_file_location("agent_collab_peer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load peer runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_text_arg(value: str | None, file_value: Path | None) -> str:
    if file_value is not None:
        return file_value.read_text(encoding="utf-8").strip()
    return (value or "").strip()


def utc_run_id(host: str, mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{host}-{mode}"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git_output(repo_root: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return ""
    return completed.stdout.strip()


def run_snapshot(repo_root: Path, output_path: Path) -> None:
    status = git_output(repo_root, ["status", "--porcelain=v1", "--untracked-files=all"])
    lines = [
        "agent_collab_git_snapshot_v1",
        f"repo={repo_root}",
        f"timestamp_utc={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"branch={git_output(repo_root, ['branch', '--show-current'])}",
        f"head={git_output(repo_root, ['rev-parse', '--verify', 'HEAD'])}",
        f"dirty={'true' if status else 'false'}",
        "-- status_porcelain_v1",
        status,
        "-- diff_name_status",
        git_output(repo_root, ["diff", "--name-status"]),
        "-- staged_name_status",
        git_output(repo_root, ["diff", "--cached", "--name-status"]),
    ]
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or default_repo_root()).resolve()
    host = args.host
    peer = "claude" if host == "codex" else "codex"
    run_id = args.run_id or utc_run_id(host, args.mode)
    run_root = (args.run_root or (repo_root / "tools" / "agent-collab" / "runs")).resolve()
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    brief = read_text_arg(args.brief, args.brief_file)
    if not brief:
        raise SystemExit("brief or brief-file is required")

    request = {
        "origin": host,
        "host": host,
        "peer": peer,
        "mode": args.mode,
        "target": args.target,
        "brief": brief,
        "edit_allowed": bool(args.edit_allowed),
        "run_id": run_id,
        "profile": args.profile,
        "local_subagents_allowed": not args.no_local_subagents,
        "max_local_subagents": args.max_local_subagents,
    }
    peer_runtime = load_peer_runtime()
    peer_runtime.validate_request(request)

    request_path = run_dir / "host-request.json"
    write_json(request_path, request)
    run_snapshot(repo_root, run_dir / "before.snapshot")

    peer_stdout = (run_dir / "peer-report.json").open("w", encoding="utf-8")
    peer_stderr = (run_dir / "peer.stderr.log").open("w", encoding="utf-8")
    env = dict(os.environ)
    env.setdefault("AGENT_COLLAB_TIMEOUT_SECONDS", str(peer_runtime.DEFAULT_AGENT_TIMEOUT_SECONDS))
    agent_timeout = peer_runtime.timeout_seconds(env)
    if agent_timeout is not None:
        env["AGENT_COLLAB_TIMEOUT_SECONDS"] = (
            str(int(agent_timeout)) if float(agent_timeout).is_integer() else str(agent_timeout)
        )
    env.setdefault("AGENT_COLLAB_PROFILE", args.profile)
    process = subprocess.Popen(
        [
            sys.executable,
            str(runtime_dir() / "peer.py"),
            str(request_path),
            "--repo-root",
            str(repo_root),
            "--raw-output",
            str(run_dir / "peer.raw.json"),
        ],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=peer_stdout,
        stderr=peer_stderr,
        env=env,
        text=True,
        start_new_session=True,
    )
    peer_stdout.close()
    peer_stderr.close()

    process_info = {
        "pid": process.pid,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "repo_root": str(repo_root),
        "host": host,
        "peer": peer,
        "profile": args.profile,
        "peer_report": str(run_dir / "peer-report.json"),
        "host_first_pass": str(run_dir / "host-first-pass.json"),
    }
    write_json(run_dir / "peer-process.json", process_info)
    print(json.dumps(process_info, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def finish(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    request = load_json(run_dir / "host-request.json")
    host_first_pass = run_dir / "host-first-pass.json"
    if not host_first_pass.exists():
        raise SystemExit(f"missing required independent host analysis: {host_first_pass}")

    process_info = load_json(run_dir / "peer-process.json")
    pid = int(process_info["pid"])
    peer_report_path = run_dir / "peer-report.json"
    if peer_report_path.exists() and peer_report_path.stat().st_size > 0:
        pass
    else:
        deadline = time.time() + args.timeout_seconds
        while time.time() < deadline:
            if peer_report_path.exists() and peer_report_path.stat().st_size > 0:
                break
            if not process_alive(pid):
                break
            time.sleep(1)

    if not peer_report_path.exists() or peer_report_path.stat().st_size == 0:
        failure = {
            "schema_version": "1.0",
            "run_id": request["run_id"],
            "origin": request["origin"],
            "host": request["host"],
            "peer": request["peer"],
            "mode": request["mode"],
            "target": request["target"],
            "status": "peer_failed",
            "verdict": "blocked",
            "summary": "Peer process produced no report before finish timeout.",
            "findings": [],
            "claims": [],
            "limitations": ["Peer report missing or empty."],
            "next_actions": [],
            "error": {"kind": "peer_no_output", "message": "Peer report missing or empty."},
        }
        write_json(peer_report_path, failure)

    peer_runtime = load_peer_runtime()
    try:
        peer_report = peer_runtime.parse_json_payload(peer_report_path.read_text(encoding="utf-8").strip())
        peer_runtime.validate_peer_report(peer_report)
        validation_status = "ok"
    except Exception as exc:
        peer_report = peer_runtime.failure("invalid_peer_report", str(exc), request)
        write_json(peer_report_path, peer_report)
        validation_status = "schema_validation_failed"

    host_report = load_json(host_first_pass)
    claim_matrix = {
        "schema_version": "1.0",
        "run_id": request["run_id"],
        "claims": [
            {**claim, "source": "host"}
            for claim in host_report.get("claims", [])
            if isinstance(claim, dict)
        ]
        + [
            {**claim, "source": "peer"}
            for claim in peer_report.get("claims", [])
            if isinstance(claim, dict)
        ],
    }
    write_json(run_dir / "claim-matrix.json", claim_matrix)

    adjudicator = {
        "schema_version": "1.0",
        "run_id": request["run_id"],
        "status": "advisory_pending",
        "summary": "Ultra profile expects a host-local adjudicator after independent reports. If no native subagent is available, the host performs this step directly.",
        "false_positives": [],
        "claims_needing_verification": [],
        "recommended_verdict": peer_report.get("verdict", "blocked"),
    }
    if not (run_dir / "adjudicator-report.json").exists():
        write_json(run_dir / "adjudicator-report.json", adjudicator)

    repo_root = Path(process_info.get("repo_root", str(default_repo_root()))).resolve()
    run_snapshot(repo_root, run_dir / "after.snapshot")
    result = {
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
        "peer_status": peer_report.get("status"),
        "validation_status": validation_status,
        "claim_matrix": str(run_dir / "claim-matrix.json"),
        "adjudicator_report": str(run_dir / "adjudicator-report.json"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def status(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    process_info = load_json(run_dir / "peer-process.json")
    pid = int(process_info["pid"])
    result = {
        "run_id": process_info["run_id"],
        "run_dir": str(run_dir),
        "peer_pid": pid,
        "peer_alive": process_alive(pid),
        "peer_report_exists": (run_dir / "peer-report.json").exists(),
        "peer_report_bytes": (run_dir / "peer-report.json").stat().st_size if (run_dir / "peer-report.json").exists() else 0,
        "host_first_pass_exists": (run_dir / "host-first-pass.json").exists(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Agent Collab peer-first host run artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)

    start_parser = sub.add_parser("start", help="Create a run directory and launch the cross-agent peer.")
    start_parser.add_argument("--host", choices=sorted(ORIGINS), required=True)
    start_parser.add_argument("--mode", choices=sorted(MODES), required=True)
    start_parser.add_argument("--target", required=True)
    start_parser.add_argument("--brief")
    start_parser.add_argument("--brief-file", type=Path)
    start_parser.add_argument("--edit-allowed", action="store_true")
    start_parser.add_argument("--profile", choices=["standard", "max", "ultra"], default="ultra")
    start_parser.add_argument("--max-local-subagents", type=int, default=6)
    start_parser.add_argument("--no-local-subagents", action="store_true")
    start_parser.add_argument("--run-id")
    start_parser.add_argument("--run-root", type=Path)
    start_parser.add_argument("--repo-root", type=Path)
    start_parser.set_defaults(func=start)

    finish_parser = sub.add_parser("finish", help="Validate peer output and write synthesis support artifacts.")
    finish_parser.add_argument("run_dir", type=Path)
    finish_parser.add_argument("--timeout-seconds", type=float, default=2700)
    finish_parser.set_defaults(func=finish)

    status_parser = sub.add_parser("status", help="Print peer process and artifact status.")
    status_parser.add_argument("run_dir", type=Path)
    status_parser.set_defaults(func=status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
