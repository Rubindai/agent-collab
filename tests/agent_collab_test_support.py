from concurrent.futures import ThreadPoolExecutor
import argparse
import filecmp
import hashlib
import importlib.util
import io
import json
import os
import re
import signal
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

CLAUDE_EFFORT_KEYS = ("CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
                      "CLAUDE_CODE_DISABLE_THINKING", "MAX_THINKING_TOKENS")

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "tools" / "agent-collab"
CODEX_PLUGIN_ROOT = REPO_ROOT / "plugins" / "agent-collab"
CODEX_SKILL_ROOT = CODEX_PLUGIN_ROOT / "skills" / "agent-collab"
CLAUDE_PLUGIN_ROOT = CODEX_PLUGIN_ROOT
CLAUDE_SKILL_ROOT = CLAUDE_PLUGIN_ROOT / "skills" / "agent-collab"
PEER_RUNTIME = RUNTIME_ROOT / "scripts" / "peer.py"
HOST_RUNTIME = RUNTIME_ROOT / "scripts" / "host.py"
STATE_RUNTIME = RUNTIME_ROOT / "scripts" / "state.py"
SNAPSHOT_RUNTIME = RUNTIME_ROOT / "scripts" / "snapshot.py"
AVAILABILITY_RUNTIME = RUNTIME_ROOT / "scripts" / "availability.py"
PEER_SCHEMA = RUNTIME_ROOT / "schemas" / "peer-report-provider.schema.json"
CANONICAL_PEER_SCHEMA = RUNTIME_ROOT / "schemas" / "peer-report.schema.json"
CANONICAL_MODES = ["debug", "design", "plan", "research", "review"]
TEST_ENV_PREFIXES = ("AGENT_COLLAB_", "CLAUDE_AGENT_COLLAB_", "CODEX_AGENT_COLLAB_")
_SAVED_TEST_ENV = {}


# Keys redirected or removed so tests never touch real user state: guard
# locks for non-Git temp roots, settings, and inherited plugin variables.
HERMETIC_ENV_KEYS = ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "CLAUDE_PLUGIN_DATA")
_HERMETIC_STATE = {}


def setUpModule():
    _SAVED_TEST_ENV.clear()
    for key in list(os.environ):
        if key.startswith(TEST_ENV_PREFIXES) or key in HERMETIC_ENV_KEYS:
            _SAVED_TEST_ENV[key] = os.environ.pop(key)
    _HERMETIC_STATE["root"] = tempfile.TemporaryDirectory(prefix="agent-collab-tests-")
    root = Path(_HERMETIC_STATE["root"].name)
    os.environ["XDG_STATE_HOME"] = str(root / "state")
    os.environ["XDG_CONFIG_HOME"] = str(root / "config")


def tearDownModule():
    for key in list(os.environ):
        if key.startswith(TEST_ENV_PREFIXES) or key in HERMETIC_ENV_KEYS:
            os.environ.pop(key)
    os.environ.update(_SAVED_TEST_ENV)
    hermetic = _HERMETIC_STATE.pop("root", None)
    if hermetic is not None:
        hermetic.cleanup()


def process_state(pid):
    """Return a process's state letter, or None once it no longer exists (Linux or macOS)."""
    stat_path = Path(f"/proc/{pid}/stat")
    if Path("/proc/self/stat").exists():
        try:
            return stat_path.read_text(encoding="utf-8").rsplit(")", 1)[1].strip()[:1]
        except FileNotFoundError:
            return None
    completed = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    state = completed.stdout.strip()
    return state[:1] if completed.returncode == 0 and state else None


def load_module(path, name):
    if path.name == "host.py":
        for dependency in ("host_config", "artifacts", "registry", "host_admin", "runs", "process_tree", "lifecycle"):
            sys.modules.pop(dependency, None)
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_peer():
    return load_module(PEER_RUNTIME, "agent_collab_peer_test")


def load_host():
    return load_module(HOST_RUNTIME, "agent_collab_host_test")


def load_state():
    return load_module(STATE_RUNTIME, "agent_collab_state_test")


def load_snapshot():
    return load_module(SNAPSHOT_RUNTIME, "agent_collab_snapshot_test")


def load_availability():
    return load_module(AVAILABILITY_RUNTIME, "agent_collab_availability_test")


def availability_attestation(peer, model, effort):
    return {
        "schema_version": "3.0",
        "status": "available",
        "peer": peer,
        "requested_model": model,
        "requested_effort": effort,
        "supported_efforts": [effort],
        "source": "claude_cli_probe" if peer == "claude" else "codex_debug_models",
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cli_version": "2.1.257" if peer == "claude" else "0.163.0",
        "effort_evidence": "stop_hook_observed" if peer == "claude" else "catalog_advertised",
        "evidence_sha256": "0" * 64,
        "details": "test availability attestation",
    }


def request(**overrides):
    peer = overrides.get("peer", "claude")
    host = overrides.get("host", "codex" if peer == "claude" else "claude")
    model = overrides.get("peer_model", "claude-opus-5-5" if peer == "claude" else "gpt-6-astra")
    effort = overrides.get("peer_effort", "xhigh")
    data = {
        "schema_version": "3.0",
        "origin": host,
        "host": host,
        "peer": peer,
        "mode": "review",
        "target": "current diff",
        "brief": "Review XML <x/>, a variable, and Unicode.",
        "edit_allowed": False,
        "run_id": "agent-collab-test",
        "adjudicator_enabled": True,
        "local_subagents_allowed": True,
        "max_local_subagents": 8,
        "online_research": True,
        "safe_mode": False,
        "workspace_mode": "direct",
        "peer_model": model,
        "peer_effort": effort,
        "availability_attestation": availability_attestation(peer, model, effort),
        "peer_timeout_seconds": 2700,
        "codex_config": [],
        "claude_tools": "default",
        "claude_max_turns": None,
    }
    data.update(overrides)
    if "helper_enforcement" not in overrides:
        enabled = data["local_subagents_allowed"] and data["max_local_subagents"] > 0
        data["helper_enforcement"] = {
            "provider": (
                "disabled"
                if not enabled
                else "claude_agent_hook" if data["peer"] == "claude" else "codex_v2_concurrency"
            ),
            "limit": data["max_local_subagents"] if enabled else 0,
            "limit_semantics": (
                "disabled"
                if not enabled
                else "total_agent_calls" if data["peer"] == "claude" else "concurrent_threads"
            ),
            "depth": (
                "disabled"
                if not enabled
                else "provider_enforced" if data["peer"] == "claude" else "advisory"
            ),
            "runtime": "overall_peer_deadline",
        }
    return data



def record_claude_stop(args, effort=None):
    if args[0] != "claude":
        return
    settings = json.loads(args[args.index("--settings") + 1])
    effort = effort or settings["env"]["CLAUDE_CODE_EFFORT_LEVEL"]
    hook = settings["hooks"]["Stop"][0]["hooks"][0]
    completed = subprocess.run(
        [hook["command"], *hook["args"]], input=json.dumps({"hook_event_name": "Stop", "effort": {"level": effort}}),
        text=True, capture_output=True,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)


def claude_result_runner(completed):
    def run(args, **kwargs):
        record_claude_stop(args)
        return completed
    return run


def valid_report(**overrides):
    req = request()
    data = {
        "schema_version": "3.0",
        "run_id": req["run_id"],
        "origin": req["origin"],
        "host": req["host"],
        "peer": req["peer"],
        "mode": req["mode"],
        "target": req["target"],
        "status": "ok",
        "verdict": "pass_with_concerns",
        "summary": "No blocking issues.",
        "findings": [],
        "claims": [{"claim": "Example", "status": "confirmed", "evidence": "test"}],
        "limitations": [],
        "next_actions": [],
        "error": None,
    }
    data.update(overrides)
    return data


def complete_settings(host, **overrides):
    data = {key: list(value) if isinstance(value, list) else value for key, value in host.CONFIG.SETTING_DEFAULTS.items()}
    data["workspace_mode"] = "direct"
    data.update(overrides)
    return data




def valid_host_first_pass(run_id="agent-collab-test"):
    return {
        "schema_version": "3.0",
        "run_id": run_id,
        "summary": "Independent host pass.",
        "claims": [{"claim": "Host claim", "status": "confirmed", "evidence": "host evidence"}],
    }


def valid_host_synthesis(run_id="agent-collab-test", run_dir=None):
    return {
        "schema_version": "3.0",
        "run_id": run_id,
        "summary": "Host synthesis is ready for the final answer.",
        "verdict": "pass_with_concerns",
        "claims": [{"claim": "Synthesized claim", "status": "confirmed", "evidence": "verified evidence"}],
        "unresolved_risks": [],
        "workspace_mutation_sha256": hashlib.sha256(b"null").hexdigest(),
        "claim_matrix_sha256": (
            json.loads((run_dir / "host-result.json").read_text())["claim_matrix_sha256"]
            if run_dir is not None else "0" * 64
        ),
        "final_answer_ready": True,
    }


def valid_process_identity(pid=43210, pgid=43210):
    # Identities from another platform are rejected, so use this host's kind.
    local_kind = (
        "linux_proc"
        if sys.platform.startswith("linux") and Path("/proc/sys/kernel/random/boot_id").is_file()
        else "posix_ps"
    )
    return {
        "kind": local_kind,
        "pid": pid,
        "pgid": pgid,
        "boot_id": "test-boot-id",
        **({"pid_namespace": os.readlink("/proc/self/ns/pid")} if local_kind == "linux_proc" else {"ps_format": "utc_c_v1"}),
        "start_time": "12345",
    }


def valid_peer_process(run_dir, repo_root, guard_path, **overrides):
    request_path = run_dir / "host-request.json"
    req = json.loads(request_path.read_text()) if request_path.exists() else request()
    data = {
        "schema_version": "3.0",
        "pid": 43210,
        "pgid": 43210,
        "process_identity": valid_process_identity(),
        "run_id": req["run_id"],
        "run_dir": str(run_dir),
        "repo_root": str(repo_root),
        "host": req["host"],
        "peer": req["peer"],
        "adjudicator_enabled": req["adjudicator_enabled"],
        "started_at": "2026-07-18T00:00:00Z",
        "started_at_epoch": 1784332800.0,
        "peer_timeout_seconds": 2700,
        "peer_cli_version": "2.1.257",
        "workspace_guard": str(guard_path),
        "owner_token": (json.loads(guard_path.read_text())["runs"][req["run_id"]]["owner_token"]
                        if guard_path.exists() else "a" * 32),
        "request_sha256": hashlib.sha256(json.dumps(req, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "settings": {"local": "local.json", "global": "global.json"},
        "peer_report": str(run_dir / "peer-report.json"),
        "peer_raw": str(run_dir / "peer.raw.json"),
        "peer_normalization": str(run_dir / "peer-normalization.json"),
        "provider_process": str(run_dir / "provider-process.json"),
        "host_first_pass": str(run_dir / "host-first-pass.json"),
    }
    data.update(overrides)
    return data


def admit_host_input(host, run_dir, filename, report):
    option = {"host-first-pass.json": "first_pass", "helper-reports.json": "helpers",
              "adjudicator-report.json": "adjudicator"}[filename]
    path = run_dir / (filename + '.input')
    host.CONFIG.STRICT_JSON.write(path, {key: value for key, value in report.items() if key not in {'schema_version', 'run_id'}})
    args = argparse.Namespace(**{option: path})
    host.LIFECYCLE.ingest_finish_inputs(args, run_dir, host.RUNS.require_run_request(run_dir),
                              host.ARTIFACTS.load_json(run_dir / 'peer-process.json'))


def author_synthesis_input(host, run_dir, report=None):
    review = host.CONFIG.REVIEW
    ready = review.prepare(run_dir, host.ARTIFACTS.load_json(run_dir / 'peer-process.json'), host.LIFECYCLE.review_documents(run_dir))
    path = Path(ready['synthesis_input'])
    report = report or valid_host_synthesis(run_dir=run_dir)
    host.CONFIG.STRICT_JSON.write(path, {key: report[key] for key in review.JUDGMENT_FIELDS})
    return path


def valid_provider_process(run_id="agent-collab-test", **overrides):
    data = {
        "schema_version": "3.0",
        "run_id": run_id,
        "pid": 54321,
        "pgid": 54321,
        "process_identity": valid_process_identity(54321, 54321),
        "status": "quiescent",
        "cleanup_outcome": "terminated",
        "completed_at": "2026-07-18T00:45:00Z",
    }
    data.update(overrides)
    return data


def valid_state_job(job_id="agent-collab-test", **overrides):
    data = {
        "created_at": "2026-07-18T00:00:00Z",
        "updated_at": "2026-07-18T00:00:00Z",
        "id": job_id,
        "run_dir": f"/tmp/runs/{job_id}",
        "repo_root": "/tmp/repo",
        "host": "codex",
        "peer": "claude",
        "mode": "review",
        "target": "current diff",
        "adjudicator_enabled": True,
        "status": "completed",
        "phase": "done",
    }
    data.update(overrides)
    return data


def valid_state_job_patch(job_id="agent-collab-test", **overrides):
    data = valid_state_job(job_id, **overrides)
    del data["created_at"]
    del data["updated_at"]
    return data










if __name__ == "__main__":
    import unittest
    unittest.main()


def claude_probe_stop(effort):
    return {
        "session_id": "00000000-0000-4000-8000-000000000000",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": str(REPO_ROOT),
        "permission_mode": "plan",
        "hook_event_name": "Stop",
        "effort": {"level": effort},
    }



def claude_probe_runner(
    model, effort, *, usage_model=None, captured=None, returncode=0, stderr=""
):
    def fake_run(*args, **kwargs):
        if captured is not None:
            captured["command"] = args[0]
            captured["kwargs"] = kwargs
        effort_path = Path(kwargs["env"]["AGENT_COLLAB_EFFORT_CAPTURE"])
        effort_path.write_text(json.dumps(claude_probe_stop(effort)), encoding="utf-8")
        envelope = {
            "type": "result",
            "is_error": returncode != 0,
            "modelUsage": {usage_model or model: {}},
        }
        return SimpleNamespace(returncode=returncode, stdout=json.dumps(envelope), stderr=stderr)

    return fake_run

