#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


sys.dont_write_bytecode = True

MAX_LOCAL_SUBAGENTS = 64
MAX_AGENT_TIMEOUT_SECONDS = 86_400
MAX_CLAUDE_TURNS = 1_000
SANDBOX_PREFLIGHT_TIMEOUT_SECONDS = 10
PROCESS_IDENTITY_KEYS = {"kind", "pid", "pgid", "boot_id", "start_time"}


def _read_linux_start_time(pid: int) -> str:
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    closing_paren = raw.rfind(")")
    if closing_paren < 0:
        raise ValueError(f"invalid /proc/{pid}/stat")
    fields_after_comm = raw[closing_paren + 2 :].split()
    # /proc/<pid>/stat field 22 is process start time in clock ticks. The
    # slice begins at field 3, so its zero-based index is 19.
    if len(fields_after_comm) <= 19:
        raise ValueError(f"incomplete /proc/{pid}/stat")
    return fields_after_comm[19]


def process_identity(pid: int) -> dict[str, Any]:
    if type(pid) is not int or pid <= 0:
        raise ValueError("pid must be a positive integer")
    pgid = os.getpgid(pid)
    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    if sys.platform.startswith("linux") and boot_id_path.is_file():
        return {
            "kind": "linux_proc",
            "pid": pid,
            "pgid": pgid,
            "boot_id": boot_id_path.read_text(encoding="utf-8").strip(),
            "start_time": _read_linux_start_time(pid),
        }

    completed = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    started = completed.stdout.strip()
    if completed.returncode != 0 or not started:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        raise RuntimeError(f"could not read process identity for pid {pid}: {detail}")
    return {
        "kind": "posix_ps",
        "pid": pid,
        "pgid": pgid,
        "boot_id": "not_available",
        "start_time": started,
    }


def validate_process_identity(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != PROCESS_IDENTITY_KEYS:
        raise ValueError(f"process identity must contain exactly {sorted(PROCESS_IDENTITY_KEYS)}")
    if value["kind"] not in {"linux_proc", "posix_ps"}:
        raise ValueError("process identity kind is invalid")
    for key in ("pid", "pgid"):
        if type(value[key]) is not int or value[key] <= 0:
            raise ValueError(f"process identity {key} must be a positive integer")
    for key in ("boot_id", "start_time"):
        if not isinstance(value[key], str) or not value[key]:
            raise ValueError(f"process identity {key} must be a non-empty string")


def process_identity_matches(expected: Any) -> bool:
    try:
        validate_process_identity(expected)
        return process_identity(int(expected["pid"])) == expected
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        return False


def _linux_process_group_members(pgid: int) -> list[tuple[int, str]]:
    members: list[tuple[int, str]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8")
            closing_paren = raw.rfind(")")
            fields = raw[closing_paren + 2 :].split()
            # fields starts at proc stat field 3: state, ppid, pgrp.
            if closing_paren < 0 or len(fields) < 3 or int(fields[2]) != pgid:
                continue
            members.append((int(entry.name), fields[0]))
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
    return members


def process_group_alive(pgid: int) -> bool:
    """Return whether a process group still has a non-zombie member."""

    if type(pgid) is not int or pgid <= 0:
        raise ValueError("pgid must be a positive integer")
    if sys.platform.startswith("linux"):
        return any(state != "Z" for _, state in _linux_process_group_members(pgid))
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process_group(
    pgid: int,
    *,
    term_timeout_seconds: float = 5.0,
    kill_timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Terminate a tracked POSIX process group and prove bounded quiescence."""

    if type(pgid) is not int or pgid <= 0:
        raise ValueError("pgid must be a positive integer")
    if pgid == os.getpgrp():
        raise ValueError("refusing to terminate the caller's own process group")
    if not process_group_alive(pgid):
        return {"outcome": "not_running", "quiescent": True, "pgid": pgid}

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return {"outcome": "not_running", "quiescent": True, "pgid": pgid}
    except PermissionError as exc:
        return {
            "outcome": "permission_denied",
            "quiescent": False,
            "pgid": pgid,
            "details": str(exc),
        }

    deadline = time.monotonic() + term_timeout_seconds
    while process_group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if not process_group_alive(pgid):
        return {"outcome": "terminated", "quiescent": True, "pgid": pgid}

    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return {"outcome": "terminated", "quiescent": True, "pgid": pgid}
    except PermissionError as exc:
        return {
            "outcome": "permission_denied",
            "quiescent": False,
            "pgid": pgid,
            "details": str(exc),
        }

    deadline = time.monotonic() + kill_timeout_seconds
    while process_group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    quiescent = not process_group_alive(pgid)
    return {
        "outcome": "killed" if quiescent else "cleanup_failed",
        "quiescent": quiescent,
        "pgid": pgid,
    }


def sandbox_preflight(repo_root: Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Exercise Linux bubblewrap once without launching a peer.

    Full-capability callers and Claude peers must not call this function. It is
    a prerequisite check for Codex read-only sandboxing on Linux. Non-Linux
    Codex safe mode relies on the provider's native sandbox and reports that no
    Linux bwrap preflight applies.
    """

    repo_root = repo_root.resolve()
    if not sys.platform.startswith("linux"):
        return {
            "status": "not_applicable",
            "backend": "provider_native",
            "details": f"Linux bwrap preflight is not applicable on {sys.platform}",
        }
    executable = shutil.which("bwrap", path=(env or os.environ).get("PATH"))
    if executable is None:
        return {
            "status": "sandbox_unavailable",
            "backend": "bwrap",
            "details": "bubblewrap executable was not found on PATH",
        }
    command = [
        executable,
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--unshare-all",
        "--die-with-parent",
        "--chdir",
        str(repo_root),
        "/usr/bin/true",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            env=dict(env or os.environ),
            text=True,
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=SANDBOX_PREFLIGHT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "sandbox_unavailable",
            "backend": "bwrap",
            "details": f"bubblewrap preflight could not run: {exc}",
        }
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-4000:]
        return {
            "status": "sandbox_unavailable",
            "backend": "bwrap",
            "details": detail or f"bubblewrap exited with status {completed.returncode}",
        }
    return {
        "status": "available",
        "backend": "bwrap",
        "details": "bubblewrap isolation preflight succeeded",
    }


def claude_agent_guard_command(counter_path: Path, limit: int) -> str:
    if type(limit) is not int or limit < 0 or limit > MAX_LOCAL_SUBAGENTS:
        raise ValueError(f"Claude Agent call limit must be between 0 and {MAX_LOCAL_SUBAGENTS}")
    return shlex.join(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "claude-agent-guard",
            "--counter",
            str(counter_path.resolve()),
            "--limit",
            str(limit),
        ]
    )


def claude_agent_guard_settings(counter_path: Path, limit: int) -> dict[str, Any]:
    return {
        "fallbackModel": [],
        "disableAllHooks": False,
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Agent",
                    "hooks": [
                        {
                            "type": "command",
                            "command": claude_agent_guard_command(counter_path, limit),
                            "timeout": 5,
                        }
                    ],
                }
            ]
        },
    }


def codex_fanout_overrides(
    *,
    local_subagents_allowed: bool,
    max_local_subagents: int,
    timeout_seconds: int | float,
) -> list[str]:
    if type(local_subagents_allowed) is not bool:
        raise ValueError("local_subagents_allowed must be boolean")
    if type(max_local_subagents) is not int or not 0 <= max_local_subagents <= MAX_LOCAL_SUBAGENTS:
        raise ValueError(f"max_local_subagents must be between 0 and {MAX_LOCAL_SUBAGENTS}")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ValueError("timeout_seconds must be numeric")
    if not 2_700 <= float(timeout_seconds) <= MAX_AGENT_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be between 2700 and {MAX_AGENT_TIMEOUT_SECONDS}")
    enabled = local_subagents_allowed and max_local_subagents > 0
    overrides = [f"features.multi_agent={'true' if enabled else 'false'}"]
    if enabled:
        overrides.extend(
            [
                f"agents.max_threads={max_local_subagents}",
                "agents.max_depth=1",
                f"agents.job_max_runtime_seconds={int(float(timeout_seconds))}",
            ]
        )
    return overrides


def _run_claude_agent_guard(counter_path: Path, limit: int) -> int:
    try:
        import fcntl

        if limit < 0 or limit > MAX_LOCAL_SUBAGENTS:
            raise ValueError("invalid Agent call limit")
        counter_path.parent.mkdir(parents=True, exist_ok=True)
        with counter_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            raw = handle.read().strip()
            count = int(raw) if raw else 0
            if count >= limit:
                print(
                    f"Agent Collab blocked Agent tool call {count + 1}: the finite per-run limit is {limit}.",
                    file=sys.stderr,
                )
                return 2
            handle.seek(0)
            handle.truncate()
            handle.write(str(count + 1))
            handle.flush()
            os.fsync(handle.fileno())
            return 0
    except BaseException as exc:
        print(f"Agent Collab could not enforce the Agent tool call limit: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal Agent Collab safety controls.")
    sub = parser.add_subparsers(dest="command", required=True)
    guard = sub.add_parser("claude-agent-guard")
    guard.add_argument("--counter", type=Path, required=True)
    guard.add_argument("--limit", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "claude-agent-guard":
        return _run_claude_agent_guard(args.counter, args.limit)
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
