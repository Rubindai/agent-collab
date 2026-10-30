"""Host settings, platform checks, and runtime resource resolution."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import validation as V
import peer as PEER
import availability as AVAILABILITY
import safety as SAFETY
import strict_json as STRICT_JSON
import snapshot as SNAPSHOT
import isolation as ISOLATION
import review as REVIEW
import state as STATE


ORIGINS = V.ORIGINS


MODES = V.MODES


ARTIFACT_SCHEMA_VERSION = "2.0"


SETTINGS_SCHEMA_VERSION = "2.0"


DEFAULT_HISTORY_RETAINED_RUNS = 50


def platform_default_workspace_mode(platform_name: str) -> str:
    """Use the supported disposable backend only where it exists."""
    return "direct" if platform_name in {"darwin", "win32"} else "isolated"


SETTING_DEFAULTS: dict[str, Any] = {
    "local_subagents_allowed": True,
    "max_local_subagents": 8,
    "adjudicator_enabled": True,
    "agent_timeout_seconds": 2700,
    "safe_mode": False,
    "workspace_mode": platform_default_workspace_mode(sys.platform),
    "codex_model": "gpt-6-astra",
    "codex_effort": "xhigh",
    "online_research": True,
    "codex_config": [],
    "claude_model": "claude-opus-5-5",
    "claude_effort": "xhigh",
    "claude_tools": "default",
    "history_retained_runs": DEFAULT_HISTORY_RETAINED_RUNS,
}


SETTING_ENV_KEYS = {
    "local_subagents_allowed": "AGENT_COLLAB_LOCAL_SUBAGENTS_ALLOWED",
    "max_local_subagents": "AGENT_COLLAB_MAX_LOCAL_SUBAGENTS",
    "adjudicator_enabled": "AGENT_COLLAB_ADJUDICATOR_ENABLED",
    "agent_timeout_seconds": "AGENT_COLLAB_TIMEOUT_SECONDS",
    "safe_mode": "AGENT_COLLAB_SAFE_MODE",
    "workspace_mode": "AGENT_COLLAB_WORKSPACE_MODE",
    "codex_model": "CODEX_AGENT_COLLAB_MODEL",
    "codex_effort": "CODEX_AGENT_COLLAB_EFFORT",
    "online_research": "AGENT_COLLAB_ONLINE_RESEARCH",
    "codex_config": "CODEX_AGENT_COLLAB_CONFIG",
    "claude_model": "CLAUDE_AGENT_COLLAB_MODEL",
    "claude_effort": "CLAUDE_AGENT_COLLAB_EFFORT",
    "claude_tools": "CLAUDE_AGENT_COLLAB_TOOLS",
    "history_retained_runs": "AGENT_COLLAB_HISTORY_RETAINED_RUNS",
}


MIN_CLI_VERSIONS = {"codex": (0, 163, 0), "claude": (2, 1, 295)}


DEFAULT_AVAILABILITY_TIMEOUT_SECONDS = 30.0


MAX_AVAILABILITY_TIMEOUT_SECONDS = 60.0


RUN_ID_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


MIN_PEER_WAIT_SECONDS = 60


FINISH_TIMEOUT_GRACE_SECONDS = 30


RESET_SCOPES = {"local", "global", "all"}


def runtime_dir() -> Path:
    return Path(__file__).resolve().parent


def resource_root() -> Path:
    return runtime_dir().parent


def repo_storage_id(repo_root: Path) -> str:
    digest = hashlib.sha256(str(repo_root).encode("utf-8")).hexdigest()[:12]
    safe_name = "".join(char if char.isalnum() or char in "._-" else "-" for char in repo_root.name).strip(".-")
    return f"{safe_name[:48] or 'repo'}-{digest}"


def default_state_root() -> Path:
    if os.environ.get("AGENT_COLLAB_STATE_HOME"):
        return Path(os.environ["AGENT_COLLAB_STATE_HOME"]).expanduser()
    # CLAUDE_PLUGIN_DATA is never exported to Bash-tool commands, so an inherited
    # value always belongs to another plugin; it must not select this state root.

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    try:
        if resource_root().resolve().is_relative_to(codex_home.resolve()):
            return codex_home / "agent-collab"
    except OSError:
        pass

    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")).expanduser()
    return base / "agent-collab"


def default_storage_root(repo_root: Path) -> Path:
    root = resource_root().resolve()
    source_root = (repo_root / "tools" / "agent-collab").resolve()
    if root == source_root:
        return source_root
    return default_state_root() / "repos" / repo_storage_id(repo_root)


def default_run_root(repo_root: Path) -> Path:
    return default_storage_root(repo_root) / "runs"


def default_local_settings_path(repo_root: Path) -> Path:
    return default_storage_root(repo_root) / "settings.local.json"


def default_global_settings_path() -> Path:
    if os.environ.get("AGENT_COLLAB_HOME"):
        return Path(os.environ["AGENT_COLLAB_HOME"]).expanduser() / "settings.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    return base / "agent-collab" / "settings.json"


def settings_path(scope: str, repo_root: Path) -> Path:
    if scope == "local":
        return default_local_settings_path(repo_root)
    if scope == "global":
        return default_global_settings_path()
    raise ValueError(f"invalid settings scope: {scope}")


GIT_PROBE_TIMEOUT_SECONDS = 30


def run_git_probe(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str] | None:
    """Run a bounded Git metadata query; None means Git is not installed.

    Inherited GIT_* variables are ignored so the worktree is always the one
    containing the working directory. A slow Git fails closed rather than
    silently selecting a different (non-Git) guard location.
    """

    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            # Git prints UTF-8 paths; surrogateescape round-trips other POSIX bytes.
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"git {' '.join(args)} exceeded {GIT_PROBE_TIMEOUT_SECONDS} seconds") from exc


def default_repo_root() -> Path:
    completed = run_git_probe(["rev-parse", "--show-toplevel"])
    if completed is not None and completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    return Path.cwd().resolve()


def canonical_repo_root(path: Path) -> Path:
    """Use one owner/storage identity for every directory in a Git worktree."""
    path = path.resolve()
    if not path.is_dir():
        raise SystemExit(f"repository path is not an existing directory: {path}")
    completed = run_git_probe(["rev-parse", "--show-toplevel"], cwd=path)
    if completed is not None and completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    if SNAPSHOT.has_git_metadata(path):
        raise SystemExit(f"cannot resolve Git worktree root for {path}; check Git and repository metadata")
    return path


def parse_cli_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", text)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def require_peer_cli_version(peer: str) -> str:
    minimum = MIN_CLI_VERSIONS[peer]
    try:
        executable = SAFETY.resolve_cli(peer)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        completed = subprocess.run(
            # Windows pins the native executable; POSIX keeps exec's lookup.
            [executable if os.name == "nt" else peer, "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"could not determine {peer} CLI version: {exc}") from exc
    raw_version = f"{completed.stdout}\n{completed.stderr}".strip()
    parsed = parse_cli_version(raw_version)
    minimum_text = ".".join(str(part) for part in minimum)
    if completed.returncode != 0 or parsed is None:
        raise SystemExit(f"could not determine {peer} CLI version; Agent Collab requires {minimum_text} or newer")
    if parsed < minimum:
        found = ".".join(str(part) for part in parsed)
        raise SystemExit(f"{peer} CLI {found} is unsupported; install {minimum_text} or newer")
    return ".".join(str(part) for part in parsed)


def configured_timeout_seconds(value: Any) -> float:
    return float(normalize_timeout(value))


def read_text_arg(value: str | None, file_value: Path | None) -> str:
    if file_value is not None:
        return STRICT_JSON.read_text(file_value, max_bytes=4 * 1024 * 1024).strip()
    return (value or "").strip()


def is_supported_platform() -> bool:
    if os.name == "nt":
        # Job Objects stand in for process groups (windows_jobs.py).
        return sys.platform == "win32"
    return (
        os.name == "posix"
        and (sys.platform.startswith("linux") or sys.platform == "darwin")
        and all(hasattr(os, name) for name in ("getpgid", "killpg"))
    )


def require_supported_platform() -> None:
    if not is_supported_platform():
        raise SystemExit(
            "Agent Collab runtime process supervision requires Linux, macOS, or Windows; "
            f"unsupported host: {sys.platform} ({os.name})"
        )


def utc_run_id(host: str, mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = os.urandom(3).hex()
    return f"{stamp}-{host}-{mode}-{suffix}"


def is_safe_run_id(run_id: str) -> bool:
    return bool(run_id) and run_id not in {".", ".."} and all(char in RUN_ID_SAFE_CHARS for char in run_id)


def require_safe_run_id(run_id: str) -> str:
    if not is_safe_run_id(run_id):
        raise ValueError("run_id must be a non-empty basename using only letters, numbers, '.', '_', or '-'")
    return run_id


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"expected boolean value, got {value!r}")


def parse_non_negative_int(value: Any, label: str) -> int:
    """Accept JSON integers or ASCII decimal strings only; never booleans or Unicode digits."""

    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip(), flags=re.ASCII):
        number = int(value.strip())
    else:
        raise ValueError(f"{label} must be a non-negative integer")
    if number < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return number


ASCII_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?", flags=re.ASCII)


def normalize_timeout(value: Any) -> float | int:
    # Strings (environment values) must be ASCII decimal numbers; float()
    # alone would also accept Unicode digits, underscores, and exponents.
    if isinstance(value, bool) or (isinstance(value, str) and not ASCII_NUMBER.fullmatch(value.strip())):
        raise ValueError("timeout must be a finite number of seconds")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be a finite number of seconds") from exc
    if not MIN_PEER_WAIT_SECONDS <= seconds <= SAFETY.MAX_AGENT_TIMEOUT_SECONDS:
        raise ValueError(
            f"timeout must be between {MIN_PEER_WAIT_SECONDS} and {SAFETY.MAX_AGENT_TIMEOUT_SECONDS} seconds"
        )
    return int(seconds) if seconds.is_integer() else seconds


def cli_integer(value: str) -> int:
    """argparse type for ASCII integers; int() would accept Unicode digits."""

    if not re.fullmatch(r"-?[0-9]+", value.strip(), flags=re.ASCII):
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}")
    return int(value)


def cli_number(value: str) -> float:
    """argparse type for ASCII decimal numbers; float() would accept Unicode digits."""

    if not ASCII_NUMBER.fullmatch(value.strip()):
        raise argparse.ArgumentTypeError(f"invalid number: {value!r}")
    return float(value)


def normalize_codex_config(value: Any, source: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = STRICT_JSON.loads(text, max_bytes=1_000_000)
            except ValueError as exc:
                raise ValueError(f"{source} codex_config must be a JSON list or key=value string") from exc
            value = parsed
        else:
            value = [item.strip() for item in text.splitlines() if item.strip()]
    if not isinstance(value, list):
        raise ValueError(f"{source} codex_config must be a list of key=value strings")

    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        key, separator, _ = text.partition("=")
        if separator != "=" or not key.strip():
            raise ValueError(f"{source} codex_config entries must use key=value syntax")
        normalized.append(text)
    SAFETY.validate_codex_config_overrides(normalized)
    return normalized


def is_fable_model(value: Any) -> bool:
    normalized = str(value).strip().casefold()
    return re.search(r"(?:^|[^a-z0-9])fable(?:$|[^a-z0-9])", normalized) is not None


def validate_claude_tools(value: Any, source: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{source} claude_tools must be a non-empty string")
    tokens = [item for item in re.split(r"[\s,]+", text) if item]
    if any(item.casefold() == "default" and item != "default" for item in tokens):
        raise ValueError(f"{source} claude_tools sentinel must be the lowercase literal 'default'")
    if "default" in tokens and tokens != ["default"]:
        raise ValueError(f"{source} claude_tools sentinel 'default' must be used alone")
    return text


def normalize_settings(raw_settings: dict[str, Any], source: str) -> dict[str, Any]:
    unknown = set(raw_settings) - set(SETTING_DEFAULTS)
    if unknown:
        raise ValueError(f"{source} contains unknown setting keys: {sorted(unknown)}")
    normalized: dict[str, Any] = {}
    for key, value in raw_settings.items():
        if key in {
            "local_subagents_allowed",
            "adjudicator_enabled",
            "safe_mode",
            "online_research",
        }:
            normalized[key] = parse_bool(value)
        elif key == "max_local_subagents":
            number = parse_non_negative_int(value, f"{source} max_local_subagents")
            if not 0 <= number <= SAFETY.MAX_LOCAL_SUBAGENTS:
                raise ValueError(
                    f"{source} max_local_subagents must be between 0 and {SAFETY.MAX_LOCAL_SUBAGENTS}"
                )
            normalized[key] = number
        elif key == "history_retained_runs":
            normalized[key] = parse_non_negative_int(value, f"{source} history_retained_runs")
        elif key in {"codex_effort", "claude_effort"}:
            text = str(value).strip()
            if not text:
                raise ValueError(f"{source} {key} must be a non-empty exact effort identifier")
            normalized[key] = text
        elif key == "codex_config":
            normalized[key] = normalize_codex_config(value, source)
        elif key == "workspace_mode":
            if value not in {"isolated", "direct"}:
                raise ValueError(f"{source} workspace_mode must be isolated or direct")
            normalized[key] = value
        elif key == "claude_tools":
            normalized[key] = validate_claude_tools(value, source)
        elif key == "agent_timeout_seconds":
            normalized[key] = normalize_timeout(value)
        else:
            text = str(value).strip()
            if not text:
                raise ValueError(f"{source} {key} must not be empty")
            if key == "claude_model" and is_fable_model(text):
                raise ValueError(
                    f"{source} claude_model cannot persist Fable; request Fable 5 explicitly "
                    "with the per-run --peer-model claude-fable-5 override"
                )
            normalized[key] = text
    return normalized


def validate_v2_settings_types(raw_settings: dict[str, Any], source: str) -> dict[str, Any]:
    """Validate persisted settings separately from the coercing environment path."""
    V.record(raw_settings, {
        **dict.fromkeys(("local_subagents_allowed", "adjudicator_enabled", "safe_mode", "online_research"), bool),
        "max_local_subagents": V.integer(0, SAFETY.MAX_LOCAL_SUBAGENTS), "history_retained_runs": V.integer(),
        **dict.fromkeys(("codex_model", "codex_effort", "claude_model", "claude_effort", "claude_tools"), V.NONBLANK),
        "workspace_mode": {"isolated", "direct"},
        "agent_timeout_seconds": V.number(MIN_PEER_WAIT_SECONDS, SAFETY.MAX_AGENT_TIMEOUT_SECONDS),
        "codex_config": [V.Rule(lambda v: type(v) is str and re.match(r"^[^=]+=", v) is not None,
                                "a key=value string")],
    }, source)
    if is_fable_model(raw_settings["claude_model"]):
        raise ValueError(
            f"{source} claude_model cannot persist Fable; use a per-run --peer-model override"
        )
    validate_claude_tools(raw_settings["claude_tools"], source)
    SAFETY.validate_codex_config_overrides(raw_settings["codex_config"])
    return {key: list(value) if isinstance(value, list) else value for key, value in raw_settings.items()}


def load_settings_file(path: Path, source: str) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, None
    try:
        data = STRICT_JSON.load(path, max_bytes=4 * 1024 * 1024)
        if not isinstance(data, dict):
            raise ValueError("settings file must contain a JSON object")
        expected_keys = {"schema_version", "updated_at", "settings"}
        if set(data) != expected_keys:
            raise ValueError(f"settings file must contain exactly {sorted(expected_keys)}")
        if data.get("schema_version") != SETTINGS_SCHEMA_VERSION:
            raise ValueError(
                f"settings schema_version must be {SETTINGS_SCHEMA_VERSION}; legacy settings are not supported"
            )
        if not isinstance(data.get("updated_at"), str) or not data["updated_at"]:
            raise ValueError("settings file updated_at must be a non-empty string")
        raw_settings = data.get("settings")
        if not isinstance(raw_settings, dict):
            raise ValueError("settings file must contain a settings object")
        if set(raw_settings) != set(SETTING_DEFAULTS):
            missing = sorted(set(SETTING_DEFAULTS) - set(raw_settings))
            unknown = sorted(set(raw_settings) - set(SETTING_DEFAULTS))
            raise ValueError(f"settings keys must match v2 exactly; missing={missing}, unknown={unknown}")
        return validate_v2_settings_types(raw_settings, source), None
    except Exception as exc:
        return {}, str(exc)


def load_settings_layers(repo_root: Path) -> dict[str, Any]:
    global_path = default_global_settings_path()
    local_path = default_local_settings_path(repo_root)
    global_settings, global_error = load_settings_file(global_path, "global settings")
    local_settings, local_error = load_settings_file(local_path, "local settings")
    return {
        "global": {"path": str(global_path), "settings": global_settings, "error": global_error},
        "local": {"path": str(local_path), "settings": local_settings, "error": local_error},
    }


def resolve_settings(repo_root: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    if env is None:
        env = os.environ
    layers = load_settings_layers(repo_root)
    effective = {
        key: list(value) if isinstance(value, list) else value
        for key, value in SETTING_DEFAULTS.items()
    }
    sources = {key: "builtin" for key in effective}
    environment_errors: list[str] = []
    for scope in ("global", "local"):
        for key, value in layers[scope]["settings"].items():
            effective[key] = value
            sources[key] = scope
    for key, env_key in SETTING_ENV_KEYS.items():
        if env_key in env:
            try:
                effective[key] = normalize_settings({key: env[env_key]}, f"environment {env_key}")[key]
            except (TypeError, ValueError) as exc:
                environment_errors.append(str(exc))
            else:
                sources[key] = f"env:{env_key}"
    return {
        "settings": effective,
        "sources": sources,
        "layers": layers,
        "environment_errors": environment_errors,
    }


def settings_error_messages(resolved: dict[str, Any]) -> list[str]:
    layer_errors = [
        f"{scope} settings ({layer['path']}): {layer['error']}"
        for scope, layer in resolved["layers"].items()
        if layer.get("error")
    ]
    return layer_errors + [
        f"environment settings: {error}"
        for error in resolved.get("environment_errors", [])
    ]


def require_valid_settings(resolved: dict[str, Any]) -> None:
    errors = settings_error_messages(resolved)
    if errors:
        detail = "\n".join(f"- {error}" for error in errors)
        raise SystemExit(
            "invalid Agent Collab settings; refusing to use defaults:\n"
            f"{detail}\n"
            "Archive the invalid settings file for evidence, remove or rename it, then rerun "
            "`host.py setup`; Agent Collab will not migrate or coerce it."
        )


def settings_to_env(settings: dict[str, Any]) -> dict[str, str]:
    environment = {}
    for key, env_key in SETTING_ENV_KEYS.items():
        value = settings[key]
        if type(SETTING_DEFAULTS[key]) is bool:
            environment[env_key] = "1" if value else "0"
        elif key == "codex_config":
            environment[env_key] = json.dumps(value, ensure_ascii=False)
        else:
            environment[env_key] = str(value)
    return environment


def write_settings_file(path: Path, settings: dict[str, Any]) -> None:
    normalized = normalize_settings(settings, str(path))
    normalized = validate_v2_settings_types(normalized, str(path))
    STRICT_JSON.write(
        path,
        {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "settings": normalized,
        },
    )


def settings_from_args(args: argparse.Namespace) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for key in SETTING_DEFAULTS:
        if key == "agent_timeout_seconds":
            continue  # The CLI alias is normalized last, preserving error order.
        value = getattr(args, key, None)
        if value is not None:
            updates[key] = value
    if getattr(args, "timeout_seconds", None) is not None:
        updates["agent_timeout_seconds"] = args.timeout_seconds
    return normalize_settings(updates, "setup arguments")


def choose_text(label: str, current: str) -> str:
    answer = input(f"{label} [{current}]\n> ").strip()
    return answer or current


def choose_codex_config(current: list[str]) -> list[str]:
    current_label = ", ".join(current) if current else "none"
    answer = input(f"Additional Codex config key=value entries [{current_label}]\n> ").strip()
    if not answer:
        return current
    if answer.lower() in {"none", "clear", "-"}:
        return []
    return [item.strip() for item in answer.split(",") if item.strip()]


def choose_bool(label: str, current: bool) -> bool:
    suffix = "Y/n" if current else "y/N"
    while True:
        answer = input(f"{label} [{suffix}]\n> ").strip().lower()
        if not answer:
            return current
        if answer in {"y", "yes", "true", "1"}:
            return True
        if answer in {"n", "no", "false", "0"}:
            return False
        print("Choose yes or no.")


def choose_timeout(current: float | int) -> float | int:
    labels = {
        2700: "45 minutes",
        3600: "60 minutes",
    }
    while True:
        current_label = labels.get(current, f"{current} seconds")
        print(f"Peer timeout [{current_label}]")
        print("  1. 45 minutes")
        print("  2. 60 minutes")
        print("  3. custom seconds")
        answer = input("> ").strip()
        if not answer:
            return current
        if answer == "1":
            return 2700
        if answer == "2":
            return 3600
        if answer == "3":
            return normalize_timeout(input("Seconds\n> ").strip())
        try:
            return normalize_timeout(answer)
        except ValueError:
            print("Choose an option or enter a positive number of seconds.")


def interactive_setup(current: dict[str, Any]) -> dict[str, Any]:
    settings = dict(current)
    full_permission = not bool(settings["safe_mode"])
    full_permission = choose_bool("Use full peer permissions by default", full_permission)
    settings["safe_mode"] = not full_permission
    settings["workspace_mode"] = choose_text(
        "Full-mode workspace (isolated or direct)", str(settings["workspace_mode"])
    )
    settings["agent_timeout_seconds"] = choose_timeout(settings["agent_timeout_seconds"])
    settings["history_retained_runs"] = int(choose_text("Terminal run history retained", str(settings["history_retained_runs"])))
    settings["local_subagents_allowed"] = choose_bool("Allow local subagents", bool(settings["local_subagents_allowed"]))
    settings["max_local_subagents"] = int(choose_text("Maximum local subagents", str(settings["max_local_subagents"])))
    settings["adjudicator_enabled"] = choose_bool(
        "Run a host-local advisory adjudicator",
        bool(settings["adjudicator_enabled"]),
    )
    settings["codex_model"] = choose_text("Codex peer model", str(settings["codex_model"]))
    settings["codex_effort"] = choose_text(
        "Codex reasoning effort (validated against the live model catalog at launch)",
        str(settings["codex_effort"]),
    )
    settings["online_research"] = choose_bool("Allow online research", bool(settings["online_research"]))
    settings["codex_config"] = choose_codex_config(list(settings["codex_config"]))
    settings["claude_model"] = choose_text("Claude peer model", str(settings["claude_model"]))
    settings["claude_effort"] = choose_text(
        "Claude effort (low, medium, high, xhigh, max, or ultracode orchestration)",
        str(settings["claude_effort"]),
    )
    settings["claude_tools"] = choose_text("Claude tool access (`default` or --tools list)", str(settings["claude_tools"]))
    return normalize_settings(settings, "interactive setup")
