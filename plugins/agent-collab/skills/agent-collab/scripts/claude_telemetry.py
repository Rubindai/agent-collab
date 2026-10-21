#!/usr/bin/env python3
"""Bounded, private effort evidence from the actual Claude peer's Stop hooks."""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "agent_collab_telemetry_strict_json", Path(__file__).with_name("strict_json.py")
)
assert _spec is not None and _spec.loader is not None
STRICT_JSON = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(STRICT_JSON)

MAX_CAPTURE_BYTES = 1_000_000
MAX_OBSERVATIONS = 1024
EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def new_receipt(run_id: str, expected_effort: str) -> dict[str, Any]:
    receipt = {
        "schema_version": "2.0", "run_id": run_id,
        "expected_effort": expected_effort, "observations": [],
    }
    validate_receipt(receipt, run_id, expected_effort)
    return receipt


def validate_receipt(value: Any, run_id: str, expected_effort: str) -> None:
    keys = {"schema_version", "run_id", "expected_effort", "observations"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Claude effort receipt must contain exactly the canonical fields")
    if (
        value["schema_version"] != "2.0" or value["run_id"] != run_id
        or not isinstance(run_id, str) or not run_id
        or value["expected_effort"] != expected_effort or expected_effort not in EFFORTS
    ):
        raise ValueError("Claude effort receipt does not match the current run and effective effort")
    observations = value["observations"]
    if not isinstance(observations, list) or len(observations) > MAX_OBSERVATIONS:
        raise ValueError("Claude effort observations must be a bounded array")
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != {"effort", "error"}:
            raise ValueError("Claude effort observation has noncanonical fields")
        effort, error = observation["effort"], observation["error"]
        if effort is not None and (not isinstance(effort, str) or effort not in EFFORTS):
            raise ValueError("Claude effort observation is invalid")
        if error is not None and (not isinstance(error, str) or not 1 <= len(error) <= 512):
            raise ValueError("Claude effort observation error is invalid")
        if effort is None and error is None:
            raise ValueError("Claude effort observation must contain effort or an explicit error")


def mismatch_details(receipt: dict[str, Any], run_id: str, expected_effort: str) -> dict[str, Any] | None:
    validate_receipt(receipt, run_id, expected_effort)
    observations = receipt["observations"]
    errors = [item["error"] for item in observations if item["error"] is not None]
    observed = sorted({item["effort"] for item in observations if item["effort"] is not None})
    if observations and not errors and all(effort == expected_effort for effort in observed):
        return None
    return {
        "expected_effort": expected_effort, "observed_efforts": observed,
        "telemetry_errors": errors,
        "reason": "Claude actual-run Stop effort evidence is missing, invalid, or differs from the exact request",
    }


def capture_args(path: Path, run_id: str, expected_effort: str) -> list[str]:
    """Return the exec-form hook arguments that follow sys.executable."""
    return [
        str(Path(__file__).resolve()), "--output", str(path.resolve()),
        "--run-id", run_id, "--expected-effort", expected_effort,
    ]


def pending_directory(path: Path) -> Path:
    return path.with_name(f"{path.stem}.pending")


def pending_capture_details(path: Path) -> dict[str, Any] | None:
    directory = pending_directory(path)
    if not directory.is_dir() or directory.is_symlink():
        return {"reason": "Claude Stop capture bookkeeping is unavailable"}
    with os.scandir(directory) as entries:
        first = next(entries, None)
    if first is not None:
        return {"reason": "A Claude Stop capture failed or did not finish", "pending_capture": first.name}
    return None


def capture(path: Path, run_id: str, expected_effort: str, raw: str) -> bool:
    """Append every observation; a later matching Stop cannot erase a mismatch."""
    directory = STRICT_JSON.ensure_private_directory(pending_directory(path))
    descriptor, marker_name = tempfile.mkstemp(prefix="stop-", dir=directory)
    marker = Path(marker_name)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        if os.name == "posix":
            os.fchmod(handle.fileno(), 0o600)
        handle.write("capture pending\n")
        handle.flush()
        os.fsync(handle.fileno())
    STRICT_JSON.fsync_directory(directory)
    # Keep the marker on any exception or interrupted hook so a prior good
    # receipt cannot conceal a later capture failure or lock timeout.
    observation: dict[str, Any] = {"effort": None, "error": None}
    try:
        payload = STRICT_JSON.loads(raw, max_bytes=MAX_CAPTURE_BYTES)
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "Stop":
            raise ValueError("expected a Claude Stop event")
        effort = payload.get("effort")
        if not isinstance(effort, dict) or not isinstance(effort.get("level"), str):
            raise ValueError("Claude Stop event did not report effort.level")
        if effort["level"] not in EFFORTS:
            raise ValueError("Claude Stop event reported an unsupported effective effort")
        observation["effort"] = effort["level"]
    except ValueError as exc:
        observation["error"] = str(exc)[:512]
    STRICT_JSON.ensure_private_directory(path.parent)
    lock_path = path.with_suffix(".lock")
    descriptor = STRICT_JSON.open_lock_file(lock_path)
    with os.fdopen(descriptor, "a+") as lock:
        deadline = time.monotonic() + 2
        while not STRICT_JSON.try_lock(lock.fileno()):
            if time.monotonic() >= deadline:
                raise TimeoutError("Claude effort receipt lock timed out")
            time.sleep(0.01)
        try:
            receipt = STRICT_JSON.load(path, max_bytes=MAX_CAPTURE_BYTES)
            validate_receipt(receipt, run_id, expected_effort)
            if len(receipt["observations"]) >= MAX_OBSERVATIONS:
                # Keep a terminal error within the finite artifact limit.
                receipt["observations"][-1] = {"effort": None, "error": "Claude Stop observation limit exceeded"}
            else:
                receipt["observations"].append(observation)
            STRICT_JSON.write(path, receipt)
            valid = mismatch_details(receipt, run_id, expected_effort) is None
            marker.unlink()
            STRICT_JSON.fsync_directory(directory)
            return valid
        finally:
            # Windows may release a closed handle's lock late; closing still
            # releases it if unlocking fails.
            try:
                STRICT_JSON.unlock(lock.fileno())
            except OSError:
                pass


def main() -> int:
    STRICT_JSON.use_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-effort", choices=sorted(EFFORTS), required=True)
    args = parser.parse_args()
    try:
        valid = capture(args.output, args.run_id, args.expected_effort, sys.stdin.read(MAX_CAPTURE_BYTES + 1))
    except (OSError, ValueError) as exc:
        print(f"Agent Collab effort evidence could not be recorded: {exc}", file=sys.stderr)
        print(STRICT_JSON.dumps({"continue": False, "stopReason": "Agent Collab effort capture failed"}))
        return 0
    if not valid:
        # Stop-hook exit 2 asks the model to continue. End the session instead;
        # the wrapper rejects the receipt independently of hook return policy.
        print(STRICT_JSON.dumps({"continue": False, "stopReason": "Agent Collab could not verify exact effort"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
