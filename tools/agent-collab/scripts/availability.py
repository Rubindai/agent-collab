#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "2.0"
PEERS = {"claude", "codex"}
CLAUDE_PROVIDER_EFFORT_CHOICES = {"low", "medium", "high", "xhigh", "max"}
# Claude Code exposes ``ultracode`` as a CLI orchestration mode. It requests
# provider effort xhigh and enables dynamic orchestration, so the request and
# the Stop-hook observation are intentionally different values.
CLAUDE_COMPOSITE_EFFORTS = {"ultracode": "xhigh"}
CLAUDE_EFFORT_CHOICES = CLAUDE_PROVIDER_EFFORT_CHOICES | set(CLAUDE_COMPOSITE_EFFORTS)
STATUSES = {"available", "unavailable", "unknown"}
SOURCES = {
    "request_contract",
    "cli_preflight",
    "codex_debug_models",
    "claude_cli_probe",
}
EFFORT_EVIDENCE = {
    "catalog_advertised",
    "stop_hook_observed",
    "none",
}
RESULT_KEYS = {
    "schema_version",
    "status",
    "peer",
    "requested_model",
    "requested_effort",
    "supported_efforts",
    "source",
    "checked_at",
    "cli_version",
    "effort_evidence",
    "evidence_sha256",
    "details",
}
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 60.0
MAX_PROBE_OUTPUT_BYTES = 16 * 1024 * 1024
EXIT_CODES = {"available": 0, "unavailable": 2, "unknown": 3}
CLAUDE_EFFORT_ENV_OVERRIDES = (
    "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
    "CLAUDE_CODE_DISABLE_THINKING",
    "MAX_THINKING_TOKENS",
)
CLAUDE_KNOWN_MODEL_EFFORTS = (
    ("claude-opus-4-8", ("low", "medium", "high", "xhigh", "max", "ultracode")),
    ("claude-fable-5", ("low", "medium", "high", "xhigh", "max", "ultracode")),
)
CLAUDE_AUXILIARY_MODEL_PREFIXES = ("claude-haiku-",)
PROBE_GATE_CODE = (
    "import os,sys;"
    "fd=int(sys.argv[1]);"
    "token=os.read(fd,1);"
    "os.close(fd);"
    "sys.exit(125) if token != b'1' else None;"
    "os.execvp(sys.argv[2],sys.argv[2:])"
)


def load_strict_json_runtime() -> Any:
    path = Path(__file__).resolve().parent / "strict_json.py"
    spec = importlib.util.spec_from_file_location("agent_collab_strict_json_availability", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load strict JSON runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STRICT_JSON = load_strict_json_runtime()


def load_safety_runtime() -> Any:
    path = Path(__file__).resolve().parent / "safety.py"
    spec = importlib.util.spec_from_file_location("agent_collab_safety_availability", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load safety runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SAFETY = load_safety_runtime()


def run_bounded_command(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run one short text probe with bounded output and descendant cleanup."""

    timeout = kwargs.pop("timeout", None)
    check = bool(kwargs.pop("check", False))
    input_text = kwargs.pop("input", None)
    stdout_target = kwargs.pop("stdout", None)
    stderr_target = kwargs.pop("stderr", None)
    if stdout_target is not subprocess.PIPE or stderr_target is not subprocess.PIPE:
        raise ValueError("bounded probes require stdout=PIPE and stderr=PIPE")
    if kwargs.get("text") is not True:
        raise ValueError("bounded probes require text=True")
    if input_text is not None and "stdin" in kwargs:
        raise ValueError("bounded probes cannot combine input with an explicit stdin")
    if input_text is not None:
        kwargs["stdin"] = subprocess.PIPE

    encoding = str(kwargs.get("encoding") or "utf-8")
    errors = str(kwargs.get("errors") or "replace")
    process: subprocess.Popen[str] | None = None
    identity: dict[str, Any] | None = None
    gate_read: int | None = None
    gate_write: int | None = None
    popen_args = args
    if os.name == "posix":
        gate_read, gate_write = os.pipe()
        os.set_inheritable(gate_read, True)
        popen_args = [sys.executable, "-c", PROBE_GATE_CODE, str(gate_read), *args]
        kwargs["pass_fds"] = tuple(kwargs.get("pass_fds", ())) + (gate_read,)

    with tempfile.TemporaryFile(mode="w+", encoding=encoding, errors=errors) as stdout_file, tempfile.TemporaryFile(
        mode="w+", encoding=encoding, errors=errors
    ) as stderr_file:
        try:
            process = subprocess.Popen(
                popen_args,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
                **kwargs,
            )
            identity = SAFETY.process_identity(process.pid)
            if gate_write is not None:
                os.write(gate_write, b"1")
                os.close(gate_write)
                gate_write = None
                os.close(gate_read)
                gate_read = None
        except BaseException:
            for descriptor in (gate_write, gate_read):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            if process is not None:
                cleanup = SAFETY.terminate_process_group(int(process.pid))
                if not cleanup["quiescent"]:
                    raise OSError(f"probe startup cleanup failed: {cleanup}")
            raise

        def write_input() -> None:
            if process is None or process.stdin is None:
                return
            try:
                if input_text is not None:
                    process.stdin.write(input_text)
                    process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass

        writer = threading.Thread(target=write_input, daemon=True)
        writer.start()
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        stop_reason: str | None = None
        while process.poll() is None:
            captured_bytes = os.fstat(stdout_file.fileno()).st_size + os.fstat(stderr_file.fileno()).st_size
            if captured_bytes > MAX_PROBE_OUTPUT_BYTES:
                stop_reason = "output_limit"
                break
            if deadline is not None and time.monotonic() >= deadline:
                stop_reason = "timeout"
                break
            time.sleep(0.02)

        returncode = process.poll()
        cleanup = SAFETY.terminate_process_group(int(identity["pgid"]))
        writer.join(timeout=1)
        if writer.is_alive() or not cleanup["quiescent"]:
            raise OSError(
                "probe process group or input writer did not become quiescent: "
                f"{dict(cleanup, input_writer_quiescent=not writer.is_alive())}"
            )
        if returncode is None:
            try:
                returncode = process.wait(timeout=1)
            except subprocess.TimeoutExpired as exc:
                raise OSError("probe leader was not reaped after process-group cleanup") from exc

        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(MAX_PROBE_OUTPUT_BYTES + 1)
        stderr = stderr_file.read(MAX_PROBE_OUTPUT_BYTES + 1)
        captured_bytes = len(stdout.encode(encoding, errors=errors)) + len(
            stderr.encode(encoding, errors=errors)
        )
        if stop_reason == "output_limit" or captured_bytes > MAX_PROBE_OUTPUT_BYTES:
            raise OSError(
                f"probe output exceeded the finite {MAX_PROBE_OUTPUT_BYTES}-byte capture limit"
            )
        if stop_reason == "timeout":
            raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)

    completed = subprocess.CompletedProcess(args, returncode, stdout, stderr)
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, args, output=stdout, stderr=stderr
        )
    return completed


def utc_timestamp() -> str:
    return datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bounded_timeout(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("availability timeout must be a number of seconds")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("availability timeout must be a number of seconds") from exc
    if not 0 < seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(
            f"availability timeout must be greater than 0 and at most {MAX_TIMEOUT_SECONDS:g} seconds"
        )
    return seconds


def effective_claude_effort(requested_effort: str) -> str:
    """Return the provider effort Claude reports for one CLI effort choice."""

    return CLAUDE_COMPOSITE_EFFORTS.get(requested_effort, requested_effort)


def result(
    *,
    status: str,
    peer: str,
    model: str,
    effort: str,
    source: str,
    cli_version: str | None,
    details: str,
    evidence: str,
    supported_efforts: list[str] | None = None,
    effort_evidence: str = "none",
) -> dict[str, Any]:
    output = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "peer": peer,
        "requested_model": model,
        "requested_effort": effort,
        "supported_efforts": list(supported_efforts or []),
        "source": source,
        "checked_at": utc_timestamp(),
        "cli_version": cli_version,
        "effort_evidence": effort_evidence,
        "evidence_sha256": hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
        "details": details,
    }
    validate_result(output)
    return output


def validate_result(output: dict[str, Any], *, require_available: bool = False) -> None:
    if not isinstance(output, dict) or set(output) != RESULT_KEYS:
        raise ValueError("availability result must contain the exact schema 2.0 key set")
    if output["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"availability schema_version must be {SCHEMA_VERSION}")
    if output["status"] not in STATUSES:
        raise ValueError(f"availability status must be one of {sorted(STATUSES)}")
    if require_available and output["status"] != "available":
        raise ValueError("persisted availability attestation must have status available")
    if output["peer"] not in PEERS:
        raise ValueError("availability peer must be claude or codex")
    for key in ("requested_model", "requested_effort"):
        if not isinstance(output[key], str):
            raise ValueError(f"availability {key} must be a string")
    for key in ("checked_at", "details"):
        if not isinstance(output[key], str) or not output[key].strip():
            raise ValueError(f"availability {key} must be a non-empty string")
    for key in ("cli_version",):
        if output[key] is not None and (not isinstance(output[key], str) or not output[key].strip()):
            raise ValueError(f"availability {key} must be null or a non-empty string")
    supported = output["supported_efforts"]
    if not isinstance(supported, list) or not all(isinstance(item, str) and item for item in supported):
        raise ValueError("availability supported_efforts must be an array of non-empty strings")
    if len(supported) != len(set(supported)):
        raise ValueError("availability supported_efforts must not contain duplicates")
    if output["source"] not in SOURCES:
        raise ValueError(f"availability source must be one of {sorted(SOURCES)}")
    if output["effort_evidence"] not in EFFORT_EVIDENCE:
        raise ValueError(f"availability effort_evidence must be one of {sorted(EFFORT_EVIDENCE)}")
    if re.fullmatch(r"[0-9a-f]{64}", str(output["evidence_sha256"])) is None:
        raise ValueError("availability evidence_sha256 must be a lowercase SHA-256 digest")
    if output["status"] == "available":
        if not output["requested_model"].strip() or not output["requested_effort"].strip():
            raise ValueError("available result must contain a non-empty requested model and effort")
        if output["cli_version"] is None:
            raise ValueError("available result must report cli_version")
        if output["requested_effort"] not in supported:
            raise ValueError("available result must include requested_effort in supported_efforts")
        expected_source = {
            "codex": "codex_debug_models",
            "claude": "claude_cli_probe",
        }[output["peer"]]
        if output["source"] != expected_source:
            raise ValueError(f"available {output['peer']} result must use source {expected_source}")
        expected_evidence = {
            "codex": "catalog_advertised",
            "claude": "stop_hook_observed",
        }[output["peer"]]
        if output["effort_evidence"] != expected_evidence:
            raise ValueError(f"available {output['peer']} result must use effort evidence {expected_evidence}")
        if output["peer"] == "claude" and output["requested_effort"] not in CLAUDE_EFFORT_CHOICES:
            raise ValueError("available Claude result must use a documented CLI effort choice")


def known_claude_efforts(model: str) -> tuple[str, ...] | None:
    normalized = model.casefold()
    for model_prefix, efforts in CLAUDE_KNOWN_MODEL_EFFORTS:
        if normalized == model_prefix or normalized.startswith(f"{model_prefix}-") or normalized.startswith(
            f"{model_prefix}["
        ):
            return efforts
    return None


def static_failure(peer: str, model: str, effort: str) -> dict[str, Any] | None:
    if not isinstance(model, str) or not model.strip():
        return result(
            status="unavailable",
            peer=peer,
            model=str(model),
            effort=effort,
            source="request_contract",
            cli_version=None,
            details="requested model must be a non-empty exact model identifier",
            evidence="empty model identifier",
            supported_efforts=sorted(CLAUDE_EFFORT_CHOICES) if peer == "claude" else [],
        )
    if not isinstance(effort, str) or not effort.strip():
        return result(
            status="unavailable",
            peer=peer,
            model=model,
            effort=effort,
            source="request_contract",
            cli_version=None,
            details="requested effort must be a non-empty exact effort identifier",
            evidence=f"empty effort: {effort!r}",
            supported_efforts=sorted(CLAUDE_EFFORT_CHOICES) if peer == "claude" else [],
        )
    # Codex efforts are model-specific and come only from the refreshed live
    # catalog. Keeping a static Codex enum here would reject future supported
    # values before the account-facing catalog could attest them.
    if peer == "codex":
        return None
    if effort not in CLAUDE_EFFORT_CHOICES:
        return result(
            status="unavailable",
            peer=peer,
            model=model,
            effort=effort,
            source="request_contract",
            cli_version=None,
            details="requested effort is not a documented Claude Code effort choice",
            evidence=f"unsupported Claude effort: {effort}",
            supported_efforts=sorted(CLAUDE_EFFORT_CHOICES),
        )
    known_efforts = known_claude_efforts(model) if peer == "claude" else None
    if known_efforts is not None and effort not in known_efforts:
        return result(
            status="unavailable",
            peer=peer,
            model=model,
            effort=effort,
            source="request_contract",
            cli_version=None,
            details="requested effort is known to be unsupported by this exact Claude model family",
            evidence=f"known unsupported model/effort pair: {model}/{effort}",
            supported_efforts=list(known_efforts),
        )
    return None


def parse_codex_model_catalog(stdout: str) -> list[dict[str, Any]]:
    """Parse the complete live JSON emitted by ``codex debug models``."""
    try:
        payload = STRICT_JSON.loads(stdout, max_bytes=16 * 1024 * 1024)
    except ValueError as exc:
        raise ValueError(f"Codex debug models emitted invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"models"}:
        raise ValueError("Codex debug models output must contain exactly one models array")
    rows = payload["models"]
    if not isinstance(rows, list):
        raise ValueError("Codex debug models models must be an array")

    models: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Codex debug models models[{index}] must be an object")
        model = row.get("slug")
        if not isinstance(model, str) or not model:
            raise ValueError(f"Codex debug models models[{index}] must contain a non-empty slug")
        if model in seen_models:
            raise ValueError(f"Codex debug models contains duplicate model slug {model!r}")
        seen_models.add(model)
        raw_efforts = row.get("supported_reasoning_levels")
        if not isinstance(raw_efforts, list):
            raise ValueError(f"Codex debug models model {model!r} must advertise supported_reasoning_levels")
        efforts: list[str] = []
        for effort_index, raw_effort in enumerate(raw_efforts):
            if not isinstance(raw_effort, dict):
                raise ValueError(
                    f"Codex debug models model {model!r} effort[{effort_index}] must be an object"
                )
            value = raw_effort.get("effort")
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"Codex debug models model {model!r} effort[{effort_index}] must identify an effort"
                )
            if value in efforts:
                raise ValueError(f"Codex debug models model {model!r} contains duplicate effort {value!r}")
            efforts.append(value)
        models.append({"model": model, "supported_efforts": efforts})
    return models


def codex_availability(
    model: str,
    effort: str,
    cli_version: str,
    repo_root: Path,
    timeout_seconds: float,
    *,
    runner: Any = subprocess.run,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    effective_runner = run_bounded_command if runner is subprocess.run else runner
    try:
        completed = effective_runner(
            ["codex", "debug", "models"],
            cwd=repo_root,
            env=dict(os.environ if env is None else env),
            text=True,
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return result(
            status="unknown",
            peer="codex",
            model=model,
            effort=effort,
            source="codex_debug_models",
            cli_version=cli_version,
            details=f"Codex debug models did not finish within {timeout_seconds:g} seconds",
            evidence=f"timeout:{exc}",
        )
    except OSError as exc:
        return result(
            status="unknown",
            peer="codex",
            model=model,
            effort=effort,
            source="codex_debug_models",
            cli_version=cli_version,
            details=f"Codex debug models could not be executed: {exc}",
            evidence=str(exc),
        )

    evidence = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        return result(
            status="unknown",
            peer="codex",
            model=model,
            effort=effort,
            source="codex_debug_models",
            cli_version=cli_version,
            details=f"Codex debug models exited with status {completed.returncode}",
            evidence=evidence,
        )
    try:
        models = parse_codex_model_catalog(completed.stdout)
    except ValueError as exc:
        return result(
            status="unknown",
            peer="codex",
            model=model,
            effort=effort,
            source="codex_debug_models",
            cli_version=cli_version,
            details=str(exc),
            evidence=evidence,
        )
    matching = [item for item in models if item["model"] == model]
    if not matching:
        return result(
            status="unavailable",
            peer="codex",
            model=model,
            effort=effort,
            source="codex_debug_models",
            cli_version=cli_version,
            details="requested exact model is absent from the live Codex model catalog",
            evidence=evidence,
        )
    supported = matching[0]["supported_efforts"]
    if effort not in supported:
        return result(
            status="unavailable",
            peer="codex",
            model=model,
            effort=effort,
            source="codex_debug_models",
            cli_version=cli_version,
            details="requested effort is not advertised for the exact model by live Codex debug models",
            evidence=evidence,
            supported_efforts=supported,
            effort_evidence="catalog_advertised",
        )
    return result(
        status="available",
        peer="codex",
        model=model,
        effort=effort,
        source="codex_debug_models",
        cli_version=cli_version,
        details="exact model and effort are advertised by live Codex debug models",
        evidence=evidence,
        supported_efforts=supported,
        effort_evidence="catalog_advertised",
    )


def model_identifier_matches(requested_model: str, observed_model: str) -> bool:
    requested = requested_model.casefold()
    observed = observed_model.casefold()
    # Natural-language aliases are resolved by the host before this function.
    # Availability and post-run attestation require the exact resolved model
    # identifier; a family, dated build, or variant must be requested by its
    # own identifier rather than accepted as a prefix match.
    return observed == requested


def observed_effort(envelope: dict[str, Any], requested_model: str) -> str | None:
    effort_keys = ("effort", "effortLevel", "effort_level", "reasoningEffort", "reasoning_effort")
    observed: list[str] = []
    for key in effort_keys:
        value = envelope.get(key)
        if isinstance(value, str) and value not in observed:
            observed.append(value)
    model_usage = envelope.get("modelUsage")
    if isinstance(model_usage, dict):
        for observed_model, usage in model_usage.items():
            if not isinstance(observed_model, str) or not model_identifier_matches(requested_model, observed_model):
                continue
            if not isinstance(usage, dict):
                continue
            for key in effort_keys:
                value = usage.get(key)
                if isinstance(value, str) and value not in observed:
                    observed.append(value)
    return observed[0] if len(observed) == 1 else None


def error_is_unavailable(text: str) -> bool:
    normalized = text.casefold()
    markers = (
        "model is not supported",
        "model not supported",
        "unsupported model",
        "model is unavailable",
        "model not available",
        "unknown model",
        "model does not exist",
        "invalid model",
        "effort is not supported",
        "effort not supported",
        "unsupported effort",
        "invalid effort",
        "unknown effort",
    )
    return any(marker in normalized for marker in markers)


def parse_claude_stop(text: str) -> str:
    try:
        payload = STRICT_JSON.loads(text, max_bytes=1_000_000)
    except ValueError as exc:
        raise ValueError(f"Claude Stop hook input was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "Stop":
        raise ValueError("Claude hook capture was not a Stop event")
    observed_effort = payload.get("effort")
    if not isinstance(observed_effort, dict):
        raise ValueError("Claude Stop hook did not report effort.level")
    level = observed_effort.get("level")
    if not isinstance(level, str) or not level:
        raise ValueError("Claude Stop hook effort.level must be a non-empty string")
    return level


def claude_availability(
    model: str,
    effort: str,
    cli_version: str,
    repo_root: Path,
    timeout_seconds: float,
    *,
    runner: Any = subprocess.run,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    probe_env = dict(os.environ if env is None else env)
    for key in CLAUDE_EFFORT_ENV_OVERRIDES:
        probe_env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="agent-collab-availability-") as temp_dir:
        effort_capture_path = Path(temp_dir) / "stop.json"
        probe_env["AGENT_COLLAB_EFFORT_CAPTURE"] = str(effort_capture_path)

        def capture_command(env_key: str) -> str:
            capture_code = (
                "import os,pathlib,sys;"
                f"pathlib.Path(os.environ[{env_key!r}]).write_text("
                "sys.stdin.read(),encoding='utf-8')"
            )
            return f"{shlex.quote(sys.executable)} -c {shlex.quote(capture_code)}"

        settings = {
            "fallbackModel": [],
            "autoMemoryEnabled": False,
            "disableAllHooks": False,
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {"type": "command", "command": capture_command("AGENT_COLLAB_EFFORT_CAPTURE")}
                        ]
                    }
                ],
            },
        }
        command = [
            "claude",
            "-p",
            "--model",
            model,
            "--effort",
            effort,
            "--permission-mode",
            "plan",
            "--max-turns",
            "1",
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--disallowedTools",
            "mcp__*",
            "--settings",
            json.dumps(settings, separators=(",", ":")),
            "--output-format",
            "json",
            # Claude's --tools option is variadic. Keep the explicit empty
            # value last so later launch controls cannot be parsed as tools.
            "--tools",
            "",
        ]
        effective_runner = run_bounded_command if runner is subprocess.run else runner
        try:
            completed = effective_runner(
                command,
                # Do not load project instructions or plugin discovery from the
                # target repository during an entitlement/selection probe.
                cwd=Path(temp_dir),
                env=probe_env,
                input="Reply with exactly AVAILABLE. Do not use tools.",
                text=True,
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return result(
                status="unknown",
                peer="claude",
                model=model,
                effort=effort,
                source="claude_cli_probe",
                cli_version=cli_version,
                details=f"Claude live availability probe did not finish within {timeout_seconds:g} seconds",
                evidence=f"timeout:{exc}",
            )
        except OSError as exc:
            return result(
                status="unknown",
                peer="claude",
                model=model,
                effort=effort,
                source="claude_cli_probe",
                cli_version=cli_version,
                details=f"Claude live availability probe could not be executed: {exc}",
                evidence=str(exc),
            )

        captures: dict[str, str] = {}
        capture_error = ""
        try:
            if effort_capture_path.stat().st_size > 1_000_000:
                raise ValueError("Claude Stop hook capture exceeded 1 MB")
            captures["Stop"] = effort_capture_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            capture_error = f"Stop: {exc}"
        capture_text = captures.get("Stop", "")
        evidence = f"{completed.stdout}\n{completed.stderr}\n{capture_text}\n{capture_error}"

    try:
        envelope = STRICT_JSON.loads(completed.stdout, max_bytes=16 * 1024 * 1024)
    except ValueError:
        envelope = None
    provider_failed = completed.returncode != 0 or (
        isinstance(envelope, dict) and envelope.get("is_error") is True
    )
    if provider_failed:
        unavailable = error_is_unavailable(evidence)
        return result(
            status="unavailable" if unavailable else "unknown",
            peer="claude",
            model=model,
            effort=effort,
            source="claude_cli_probe",
            cli_version=cli_version,
            details=(
                "Claude explicitly rejected the requested exact model or effort"
                if unavailable
                else f"Claude live availability probe failed with status {completed.returncode}"
            ),
            evidence=evidence,
        )
    if capture_error:
        return result(
            status="unknown",
            peer="claude",
            model=model,
            effort=effort,
            source="claude_cli_probe",
            cli_version=cli_version,
            details=f"Claude availability hook telemetry was unavailable: {capture_error}",
            evidence=evidence,
        )
    if not isinstance(envelope, dict):
        return result(
            status="unknown",
            peer="claude",
            model=model,
            effort=effort,
            source="claude_cli_probe",
            cli_version=cli_version,
            details="Claude live probe did not return one JSON result envelope",
            evidence=evidence,
        )
    model_usage = envelope.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage or not all(
        isinstance(item, str) and item for item in model_usage
    ):
        return result(
            status="unknown",
            peer="claude",
            model=model,
            effort=effort,
            source="claude_cli_probe",
            cli_version=cli_version,
            details="Claude live probe did not expose an unambiguous modelUsage object",
            evidence=evidence,
        )
    usage_models = sorted(model_usage)
    try:
        reported_effort = parse_claude_stop(captures["Stop"])
    except ValueError as exc:
        return result(
            status="unknown",
            peer="claude",
            model=model,
            effort=effort,
            source="claude_cli_probe",
            cli_version=cli_version,
            details=str(exc),
            evidence=evidence,
        )
    unexpected_usage = [
        item
        for item in usage_models
        if not model_identifier_matches(model, item)
        and not item.casefold().startswith(CLAUDE_AUXILIARY_MODEL_PREFIXES)
    ]
    matching_usage = [item for item in usage_models if model_identifier_matches(model, item)]
    model_mismatch = bool(unexpected_usage) or not matching_usage
    expected_reported_effort = effective_claude_effort(effort)
    if model_mismatch or reported_effort != expected_reported_effort:
        return result(
            status="unavailable",
            peer="claude",
            model=model,
            effort=effort,
            source="claude_cli_probe",
            cli_version=cli_version,
            details=(
                "Claude resolved a model or effective effort different from the exact requested pair; "
                f"modelUsage={usage_models}; requested_cli_effort={effort}; "
                f"expected_provider_effort={expected_reported_effort}; observed_effort={reported_effort}"
            ),
            evidence=evidence,
            supported_efforts=[effort] if reported_effort == expected_reported_effort else [],
            effort_evidence="stop_hook_observed",
        )
    return result(
        status="available",
        peer="claude",
        model=model,
        effort=effort,
        source="claude_cli_probe",
        cli_version=cli_version,
        details=(
            "Claude live modelUsage reported the exact requested model and the Stop hook reported "
            f"effective provider effort {reported_effort} for CLI effort {effort}"
        ),
        evidence=evidence,
        supported_efforts=[effort],
        effort_evidence="stop_hook_observed",
    )


def check(
    peer: str,
    model: str,
    effort: str,
    cli_version: str,
    repo_root: Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    *,
    runner: Any = subprocess.run,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    static = static_failure(peer, model, effort)
    if static is not None:
        return static
    timeout = bounded_timeout(timeout_seconds)
    if peer == "codex":
        return codex_availability(model, effort, cli_version, repo_root, timeout, runner=runner, env=env)
    return claude_availability(model, effort, cli_version, repo_root, timeout, runner=runner, env=env)
