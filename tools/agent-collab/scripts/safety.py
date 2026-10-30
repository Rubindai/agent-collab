#!/usr/bin/env python3
from __future__ import annotations

import argparse
from importlib import import_module as _load_sibling
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


sys.dont_write_bytecode = True
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_LOCAL_SUBAGENTS = 64
# A cold interpreter start can take seconds on Windows and macOS; a timed-out
# Agent guard blocks a legitimate call, and a timed-out Stop capture fails the
# run for missing effort evidence. The peer deadline still bounds both.
CLAUDE_HOOK_TIMEOUT_SECONDS = 30
MAX_AGENT_TIMEOUT_SECONDS = 86_400
SANDBOX_PREFLIGHT_TIMEOUT_SECONDS = 10
PROCESS_IDENTITY_KEYS = {"kind", "pid", "pgid", "boot_id", "start_time"}
# Top-level Codex 0.163.0 config namespaces that select the model, provider,
# endpoint, auth, catalog, approval/reviewer, sandbox, research, memory, or
# helper behavior. Owning them keeps user -c entries from detaching the launch
# from its attestation; every nested key is owned with its namespace.
AGENT_COLLAB_OWNED_CODEX_CONFIG_KEYS = {
    "agents",
    "approval_policy",
    "approvals_reviewer",
    "apps_mcp_product_sku",
    "auto_review",
    "chatgpt_base_url",
    "cli_auth_credentials_store",
    "daybreak",
    "default_permissions",
    "experimental_realtime_webrtc_call_base_url",
    "experimental_realtime_ws_base_url",
    "experimental_realtime_ws_model",
    "features",
    "forced_chatgpt_workspace_id",
    "forced_login_method",
    "memories",
    "model",
    "model_catalog_json",
    "model_provider",
    "model_providers",
    "model_reasoning_effort",
    "openai_base_url",
    "oss_provider",
    "permissions",
    "plan_mode_reasoning_effort",
    "profile",
    "profiles",
    "responses_api_metadata",
    "review_model",
    "sandbox_mode",
    "sandbox_workspace_write",
    "service_tier",
    "tools",
    "web_search",
}
CANONICAL_CODEX_CONFIG_KEY = re.compile(
    r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\Z"
)
PROCESS_IDENTITY_KINDS = {"linux_proc", "posix_ps", "windows_process"}
# A gated child blocks on one byte from an inherited pipe before starting the
# real command, so the parent can commit the child's exact identity first.
# POSIX execs in place and keeps the PID. Windows has no exec, so the
# trampoline stays alive as the job root and relays the exit status.
POSIX_GATE_CODE = """import os, sys
gate_fd = int(sys.argv[1])
try:
    token = os.read(gate_fd, 1)
finally:
    os.close(gate_fd)
if token != b"1":
    raise SystemExit(125)
os.execvp(sys.argv[2], sys.argv[2:])
"""
WINDOWS_GATE_CODE = """import msvcrt, os, subprocess, sys
gate_fd = msvcrt.open_osfhandle(int(sys.argv[1]), os.O_RDONLY)
try:
    token = os.read(gate_fd, 1)
finally:
    os.close(gate_fd)
if token != b"1":
    raise SystemExit(125)
code = subprocess.call(sys.argv[2:], stdin=0, stdout=1, stderr=2)
# Keep NTSTATUS codes intact where SystemExit takes a signed 32-bit value.
raise SystemExit(code - 2**32 if code >= 2**31 else code)
"""


def _read_linux_start_time(pid: int) -> str:
    return _read_linux_stat(pid)[19]


def _read_linux_stat(pid: int) -> list[str]:
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    closing_paren = raw.rfind(")")
    if closing_paren < 0:
        raise ValueError(f"invalid /proc/{pid}/stat")
    fields_after_comm = raw[closing_paren + 2 :].split()
    # /proc/<pid>/stat field 22 is process start time in clock ticks. The
    # slice begins at field 3, so its zero-based index is 19.
    if len(fields_after_comm) <= 19:
        raise ValueError(f"incomplete /proc/{pid}/stat")
    return fields_after_comm


def pid_namespace() -> str:
    """Identify the namespace in which recorded numeric PIDs are observed."""
    return os.readlink("/proc/self/ns/pid")


def ps_environment() -> dict[str, str]:
    return {**os.environ, "TZ": "UTC", "LC_ALL": "C"}


def process_identity(pid: int) -> dict[str, Any]:
    if type(pid) is not int or pid <= 0:
        raise ValueError("pid must be a positive integer")
    if os.name == "nt":
        # A Windows group is the job rooted at this process (windows_jobs.py),
        # and creation time is unique across reboots.
        return {
            "kind": "windows_process",
            "pid": pid,
            "pgid": pid,
            "boot_id": "not_available",
            "start_time": _load_sibling("windows_jobs").process_start_time(pid),
        }
    pgid = os.getpgid(pid)
    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    if sys.platform.startswith("linux") and boot_id_path.is_file():
        return {
            "kind": "linux_proc",
            "pid": pid,
            "pgid": pgid,
            "boot_id": boot_id_path.read_text(encoding="utf-8").strip(),
            "start_time": _read_linux_start_time(pid),
            "pid_namespace": pid_namespace(),
        }

    completed = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        env=ps_environment(),
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
        "ps_format": "utc_c_v1",
    }


def local_identity_kind() -> str:
    """Return the identity kind process_identity produces on this host."""

    if os.name == "nt":
        return "windows_process"
    if sys.platform.startswith("linux") and Path("/proc/sys/kernel/random/boot_id").is_file():
        return "linux_proc"
    return "posix_ps"


def identity_is_foreign(value: Mapping[str, Any]) -> bool:
    """Return whether another platform recorded this identity, so its PID means nothing here."""

    return value["kind"] != local_identity_kind()


def validate_process_identity(value: Any) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str) or value["kind"] not in PROCESS_IDENTITY_KINDS:
        raise ValueError("process identity kind is invalid")
    keys = PROCESS_IDENTITY_KEYS | ({"pid_namespace"} if value["kind"] == "linux_proc"
                                   else {"ps_format"} if value["kind"] == "posix_ps" else set())
    if set(value) != keys:
        raise ValueError(f"process identity must contain exactly {sorted(keys)}")
    if value["kind"] == "posix_ps" and value["ps_format"] != "utc_c_v1":
        raise ValueError("process identity ps_format must identify UTC and C locale")
    if value["kind"] == "linux_proc" and (
        not isinstance(value["pid_namespace"], str)
        or re.fullmatch(r"pid:\[[0-9]+\]", value["pid_namespace"]) is None
    ):
        raise ValueError("process identity pid_namespace must identify a Linux PID namespace")
    for key in ("pid", "pgid"):
        if type(value[key]) is not int or value[key] <= 0:
            raise ValueError(f"process identity {key} must be a positive integer")
    for key in ("boot_id", "start_time"):
        if not isinstance(value[key], str) or not value[key]:
            raise ValueError(f"process identity {key} must be a non-empty string")


def process_identity_state(expected: Any) -> str:
    """Distinguish proven termination/replacement from unavailable evidence."""
    try:
        validate_process_identity(expected)
    except ValueError:
        return "unknown"
    if identity_is_foreign(expected):
        # For example native Windows and WSL sharing a worktree.
        return "unknown"
    if expected["kind"] == "linux_proc":
        try:
            if expected["pid_namespace"] != pid_namespace():
                return "unknown"
        except OSError:
            return "unknown"
    try:
        observed = process_identity(expected["pid"])
    except ProcessLookupError:
        return "dead_or_replaced"
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        # A failed /proc, ps, or Windows read is not proof that the launcher
        # has exited.
        try:
            if not pid_alive(expected["pid"]):
                return "dead_or_replaced"
        except OSError:
            pass
        return "unknown"
    return "matching" if observed == expected and pid_alive(expected["pid"]) else "dead_or_replaced"


def pid_alive(pid: int) -> bool:
    """Return whether pid names a live process; permission errors count as live."""

    if os.name == "nt":
        # os.kill(pid, 0) would send CTRL_C_EVENT on Windows.
        return _load_sibling("windows_jobs").pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if sys.platform.startswith("linux"):
        try:
            return _read_linux_stat(pid)[0] != "Z"
        except (FileNotFoundError, ProcessLookupError):
            return False
        except (OSError, ValueError):
            return True
    try:
        completed = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], env=ps_environment(),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return not completed.stdout.strip().startswith("Z")
    except (OSError, subprocess.SubprocessError):
        pass
    return True


def process_identity_matches(expected: Any) -> bool:
    return process_identity_state(expected) == "matching"


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


def _ps_process_group_members(pgid: int) -> list[tuple[int, str]]:
    completed = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "pgid=", "-o", "stat="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ps exited with status {completed.returncode}")
    members: list[tuple[int, str]] = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == str(pgid):
            members.append((int(fields[0]), fields[2]))
    return members


def process_group_alive(pgid: int) -> bool:
    """Return whether a process group still has a non-zombie member."""

    if type(pgid) is not int or pgid <= 0:
        raise ValueError("pgid must be a positive integer")
    if os.name == "nt":
        return _load_sibling("windows_jobs").group_alive(pgid)
    if sys.platform.startswith("linux"):
        return any(state != "Z" for _, state in _linux_process_group_members(pgid))
    try:
        # A kill probe cannot tell a zombie-only group from a live one on
        # macOS, which answers EPERM for both.
        return any(not state.startswith("Z") for _, state in _ps_process_group_members(pgid))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        pass
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_denied(pgid: int, exc: PermissionError) -> dict[str, Any] | None:
    """Return the failure for EPERM from killpg, or None to keep waiting.

    Linux reports EPERM only when no member could be signaled. macOS also
    reports it when the group holds an unreaped zombie while still signaling
    the live members, so there the liveness check decides.
    """

    if not sys.platform.startswith("linux"):
        return None
    return {"outcome": "permission_denied", "quiescent": False, "pgid": pgid, "details": str(exc)}


def terminate_process_group(
    pgid: int,
    *,
    term_timeout_seconds: float = 5.0,
    kill_timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Terminate a tracked process group and prove bounded quiescence."""

    if type(pgid) is not int or pgid <= 0:
        raise ValueError("pgid must be a positive integer")
    if os.name == "nt":
        if pgid == os.getpid():
            raise ValueError("refusing to terminate the caller's own process group")
        return _load_sibling("windows_jobs").terminate_group(pgid, kill_timeout_seconds)
    if pgid == os.getpgrp():
        raise ValueError("refusing to terminate the caller's own process group")
    if not process_group_alive(pgid):
        return {"outcome": "not_running", "quiescent": True, "pgid": pgid}

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return {"outcome": "not_running", "quiescent": True, "pgid": pgid}
    except PermissionError as exc:
        denied = _signal_denied(pgid, exc)
        if denied is not None:
            return denied

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
        denied = _signal_denied(pgid, exc)
        if denied is not None:
            return denied

    deadline = time.monotonic() + kill_timeout_seconds
    while process_group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    quiescent = not process_group_alive(pgid)
    return {
        "outcome": "killed" if quiescent else "cleanup_failed",
        "quiescent": quiescent,
        "pgid": pgid,
    }


def popen_group(args: list[str], *, detached: bool = False, **kwargs: Any) -> subprocess.Popen[Any]:
    """Start args as the root of a new tracked process group.

    POSIX starts a new session, so the child's PID is its PGID. Windows makes
    the child the root of a named Job Object (windows_jobs.py). A detached
    Windows root also breaks away from the caller's job, whose closure would
    otherwise end the run with the caller's command, and fails closed when
    that job forbids breakaway.
    """

    if os.name != "nt":
        return subprocess.Popen(args, start_new_session=True, **kwargs)
    jobs = _load_sibling("windows_jobs")
    # The root starts suspended so nothing it launches predates its job.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | jobs.CREATE_SUSPENDED
    if detached:
        flags |= subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB
    try:
        process = subprocess.Popen(args, creationflags=flags, **kwargs)
    except PermissionError as exc:
        if not detached:
            raise
        raise PermissionError(
            "could not start a detached process outside the caller's Windows job; "
            f"the job may forbid breakaway: {exc}"
        ) from exc
    try:
        jobs.adopt(process.pid)
        jobs.resume(process.pid)
    except BaseException:
        process.kill()
        process.wait()
        raise
    return process


def pass_gate(read_fd: int, popen_kwargs: dict[str, Any]) -> int:
    """Let only the next child inherit a gate's read end; return its token."""

    if os.name == "nt":
        import msvcrt

        handle = msvcrt.get_osfhandle(read_fd)
        os.set_handle_inheritable(handle, True)
        startupinfo = popen_kwargs.setdefault("startupinfo", subprocess.STARTUPINFO())
        attributes = dict(startupinfo.lpAttributeList or {})
        attributes["handle_list"] = [*attributes.get("handle_list", ()), handle]
        startupinfo.lpAttributeList = attributes
        return handle
    os.set_inheritable(read_fd, True)
    popen_kwargs["pass_fds"] = (*popen_kwargs.get("pass_fds", ()), read_fd)
    return read_fd


def receive_gate(token: int) -> int:
    """Return the child's descriptor for a token from pass_gate."""

    if os.name == "nt":
        import msvcrt

        return msvcrt.open_osfhandle(token, os.O_RDONLY)
    return token


def gated_command(args: list[str], read_fd: int, popen_kwargs: dict[str, Any]) -> list[str]:
    """Wrap args so they start only after the gate receives one byte."""

    token = pass_gate(read_fd, popen_kwargs)
    code = WINDOWS_GATE_CODE if os.name == "nt" else POSIX_GATE_CODE
    # The trampoline needs only the stdlib: ignore the working directory,
    # Python environment variables, and site hooks.
    return [sys.executable, "-I", "-S", "-c", code, str(token), *args]


def resolve_cli(name: str, path: str | None = None) -> str:
    """Find a provider CLI on PATH, or raise FileNotFoundError with the reason.

    Windows accepts only a native executable and skips the implicit
    current-directory search: cmd.exe re-parses the arguments of .cmd and
    .bat shims such as npm's, which corrupts inline JSON settings and quoted
    config values.
    """

    if os.name != "nt":
        resolved = shutil.which(name, path=path)
        if resolved is None:
            raise FileNotFoundError(f"required CLI not found on PATH: {name}")
        return resolved
    search_path = os.environ.get("PATH", "") if path is None else path
    extensions = [item.lower() for item in (os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(";") if item]
    for directory in search_path.split(os.pathsep):
        directory = directory.strip().strip('"')
        if not directory:
            continue
        for extension in extensions:
            candidate = Path(directory) / f"{name}{extension}"
            if not candidate.is_file():
                continue
            if extension != ".exe":
                raise FileNotFoundError(
                    f"{name} resolves to {candidate}, which is not a native executable; "
                    f"install {name} with its official Windows installer so that {name}.exe "
                    "comes first on PATH"
                )
            return str(candidate)
    raise FileNotFoundError(f"required CLI not found on PATH: {name}")


def native_command(args: list[str], env: Mapping[str, str] | None = None) -> list[str]:
    """Pin a provider command to its native executable on Windows.

    POSIX keeps exec's own PATH lookup.
    """

    if os.name != "nt":
        return list(args)
    return [resolve_cli(args[0], (os.environ if env is None else env).get("PATH")), *args[1:]]


def sandbox_preflight(repo_root: Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Exercise Codex's Linux sandbox once without launching a model.

    Full-capability callers and Claude peers must not call this function. It is
    a prerequisite check for Codex read-only sandboxing on Linux. Non-Linux
    Codex safe mode relies on the provider's native sandbox and reports that no
    Linux bwrap preflight applies.
    """

    repo_root = repo_root.resolve()
    if os.name == "nt":
        # Codex runs native Windows commands without an OS sandbox unless its
        # own Windows sandbox is configured, which Agent Collab cannot verify.
        return {
            "status": "sandbox_unavailable",
            "backend": "windows",
            "details": (
                "Codex safe mode on native Windows has no Agent Collab-verified read-only sandbox; "
                "use full mode, a Claude peer, or WSL2"
            ),
        }
    if not sys.platform.startswith("linux"):
        return {
            "status": "not_applicable",
            "backend": "provider_native",
            "details": f"Linux bwrap preflight is not applicable on {sys.platform}",
        }
    # Let the installed CLI resolve its own bundled backend, including npm's
    # native optional dependency. Guessing paths beside the npm JS launcher
    # misses that backend. An explicit built-in profile tests OS capability
    # without managed requirements remapping it to a different profile. This
    # no-op is not a peer launch; actual peers still load managed policy.
    command = [
        "codex",
        "sandbox",
        "--permission-profile",
        ":read-only",
        "--cd",
        str(repo_root),
        "--",
        sys.executable,
        "-c",
        "pass",
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
            "details": f"Codex sandbox preflight could not run: {exc}",
        }
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-4000:]
        return {
            "status": "sandbox_unavailable",
            "backend": "bwrap",
            "details": detail or f"Codex sandbox exited with status {completed.returncode}",
        }
    return {
        "status": "available",
        "backend": "bwrap",
        "details": "Codex read-only sandbox initialization succeeded",
    }


def claude_agent_guard_args(counter_path: Path, limit: int) -> list[str]:
    """Return the exec-form arguments that follow sys.executable in the hook."""

    if type(limit) is not int or limit < 0 or limit > MAX_LOCAL_SUBAGENTS:
        raise ValueError(f"Claude Agent call limit must be between 0 and {MAX_LOCAL_SUBAGENTS}")
    return [
        str(Path(__file__).resolve()),
        "claude-agent-guard",
        "--counter",
        str(counter_path.resolve()),
        "--limit",
        str(limit),
    ]


def claude_agent_guard_settings(
    counter_path: Path,
    limit: int,
    model: str,
) -> dict[str, Any]:
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Claude model must be a non-empty exact identifier")
    return {
        "availableModels": [model],
        "fallbackModel": [],
        "switchModelsOnFlag": False,
        "crossSessionInbound": "refuse",
        "disableWorkflows": True,
        "ultracode": False,
        # Prior auto-memory notes would leak earlier conclusions into an
        # independent peer review.
        "autoMemoryEnabled": False,
        "disableAllHooks": False,
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Agent",
                    "hooks": [
                        {
                            # Exec form runs without a shell on every
                            # platform, so paths need no quoting.
                            "type": "command",
                            "command": sys.executable,
                            "args": claude_agent_guard_args(counter_path, limit),
                            "timeout": CLAUDE_HOOK_TIMEOUT_SECONDS,
                            "onFailure": "block",
                        }
                    ],
                }
            ]
        },
    }


def claude_helper_policy_failure(model: str, effort: str) -> dict[str, Any] | None:
    if effort != "ultracode":
        return None
    return {
        "schema_version": "2.0",
        "status": "helper_policy_unavailable",
        "peer": "claude",
        "requested_model": model,
        "requested_effort": effort,
        "details": (
            "Explicit ultracode requires Workflow orchestration, whose agents bypass the finite "
            "Agent-call counter. Agent Collab cannot enforce its helper bound for this mode. "
            "No peer was launched and no model or effort fallback was selected."
        ),
    }


def codex_fanout_overrides(
    *,
    local_subagents_allowed: bool,
    max_local_subagents: int,
    model: str,
    effort: str,
) -> list[str]:
    if type(local_subagents_allowed) is not bool:
        raise ValueError("local_subagents_allowed must be boolean")
    if type(max_local_subagents) is not int or not 0 <= max_local_subagents <= MAX_LOCAL_SUBAGENTS:
        raise ValueError(f"max_local_subagents must be between 0 and {MAX_LOCAL_SUBAGENTS}")
    if not isinstance(model, str) or not model.strip() or not isinstance(effort, str) or not effort.strip():
        raise ValueError("Codex helper model and effort must be non-empty strings")
    enabled = local_subagents_allowed and max_local_subagents > 0
    if not enabled:
        # An enabled multi_agent_v2 feature outranks agents.enabled=false.
        return ["agents.enabled=false", "features.multi_agent_v2=false"]
    return [
        "agents.enabled=true",
        f"agents.max_concurrent_threads_per_session={max_local_subagents}",
        # Models whose catalog entry selects multi-agent V1 nest by this
        # depth; V2 ignores it.
        "agents.max_depth=1",
        # V2 counts the root thread and reads its own table before the agents
        # value, so a user config.toml table could otherwise widen the cap.
        f"features.multi_agent_v2.max_concurrent_threads_per_session={max_local_subagents + 1}",
        "features.multi_agent_v2.expose_spawn_agent_model_overrides=false",
        f"agents.default_subagent_model={json.dumps(model)}",
        f"agents.default_subagent_reasoning_effort={json.dumps(effort)}",
    ]


def codex_launch_policy_overrides() -> list[str]:
    """Pin exec approval and provider memory independently of user config.

    The root --ask-for-approval flag configures only the interactive CLI; exec
    keeps its headless Never default only while the reviewer is the user.
    Provider memory would leak conclusions from earlier sessions into an
    independent peer.
    """

    return [
        'approval_policy="never"',
        'approvals_reviewer="user"',
        "features.memories=false",
        # Canonical subsystem controls still suppress memory if an inherited
        # provider feature alias re-enables the feature during config merging.
        "memories.use_memories=false",
        "memories.generate_memories=false",
        # A configured remote board can receive external context even in an
        # ephemeral session. Keep ordinary native helper messaging available.
        "features.agent_message_board=false",
    ]


def helper_enforcement(
    peer: str,
    *,
    local_subagents_allowed: bool,
    max_local_subagents: int,
) -> dict[str, Any]:
    if peer not in {"claude", "codex"}:
        raise ValueError("peer must be claude or codex")
    if type(local_subagents_allowed) is not bool:
        raise ValueError("local_subagents_allowed must be boolean")
    if type(max_local_subagents) is not int or not 0 <= max_local_subagents <= MAX_LOCAL_SUBAGENTS:
        raise ValueError(f"max_local_subagents must be between 0 and {MAX_LOCAL_SUBAGENTS}")
    enabled = local_subagents_allowed and max_local_subagents > 0
    if not enabled:
        return {
            "provider": "disabled",
            "limit": 0,
            "limit_semantics": "disabled",
            "depth": "disabled",
            "runtime": "overall_peer_deadline",
        }
    if peer == "claude":
        return {
            "provider": "claude_agent_hook",
            "limit": max_local_subagents,
            "limit_semantics": "total_agent_calls",
            "depth": "provider_enforced",
            "runtime": "overall_peer_deadline",
        }
    return {
        "provider": "codex_v2_concurrency",
        "limit": max_local_subagents,
        "limit_semantics": "concurrent_threads",
        "depth": "advisory",
        "runtime": "overall_peer_deadline",
    }


def validate_codex_config_overrides(values: list[str]) -> None:
    if not isinstance(values, list):
        raise ValueError("codex_config must be a list")
    for value in values:
        if not isinstance(value, str):
            raise ValueError("codex_config entries must be strings")
        key, separator, _ = value.partition("=")
        if separator != "=" or not key:
            raise ValueError("codex_config entries must use key=value syntax")
        if CANONICAL_CODEX_CONFIG_KEY.fullmatch(key) is None:
            raise ValueError(
                "codex_config keys must use canonical bare dotted syntax "
                "(letters, numbers, '_', and '-' only)"
            )
        if any(
            key == owned or key.startswith(f"{owned}.") or owned.startswith(f"{key}.")
            for owned in AGENT_COLLAB_OWNED_CODEX_CONFIG_KEYS
        ):
            raise ValueError(
                f"codex_config may not override Agent Collab-owned key or namespace {key!r}"
            )


def _run_claude_agent_guard(counter_path: Path, limit: int) -> int:
    try:
        strict_json = _load_sibling("strict_json")
        if limit < 0 or limit > MAX_LOCAL_SUBAGENTS:
            raise ValueError("invalid Agent call limit")
        strict_json.ensure_private_directory(counter_path.parent)
        flags = os.O_RDWR | strict_json.BINARY_FLAG
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        lock_descriptor = strict_json.open_lock_file(counter_path.with_name(counter_path.name + '.lock'))
        with os.fdopen(lock_descriptor, 'a+') as lock:
            # The hook's own timeout bounds this wait.
            while not strict_json.try_lock(lock.fileno()):
                time.sleep(0.01)
            try:
                try:
                    descriptor = os.open(counter_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                    created = True
                except FileExistsError:
                    descriptor = os.open(counter_path, flags, 0o600)
                    created = False
                if os.name == "posix":
                    os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
                    handle.seek(0)
                    raw = handle.read(64)
                    if (not raw and not created) or (raw and (re.fullmatch(r"0|[1-9][0-9]*", raw) is None or len(raw) > 2)):
                        raise ValueError("Agent call counter must be a canonical non-negative integer")
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
            finally:
                # Windows may release a closed handle's lock late; closing
                # still releases it if unlocking fails.
                try:
                    strict_json.unlock(lock.fileno())
                except OSError:
                    pass
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
    _load_sibling("strict_json").use_utf8_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "claude-agent-guard":
        return _run_claude_agent_guard(args.counter, args.limit)
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
