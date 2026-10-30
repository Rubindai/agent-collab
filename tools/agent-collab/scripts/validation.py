"""Small shared checks for Python values decoded from strict JSON.

Records declare required and optional fields. Types are exact (bool is never
an integer); lists contain one item rule, tuples are alternatives, and sets
are literal choices. Cross-artifact and lifecycle checks stay with callers.
This is not a JSON Schema interpreter.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Rule:
    accepts: Callable[[Any], bool]
    description: str


ORIGINS = {"claude", "codex"}
MODES = {"review", "research", "design", "plan", "debug"}
VERDICTS = {"pass", "pass_with_concerns", "changes_recommended", "ready",
            "needs_revision", "blocked", "informational"}
CLAIM_STATUSES = {"confirmed", "plausible_unverified", "rejected", "product_decision", "needs_human_input"}
VALIDATION_STATUSES = {"ok", "invalid_json", "noncanonical_output", "schema_validation_failed", "peer_report_mismatch"}
FINISH_TIMEOUT_SOURCES = {"explicit", "peer_timeout_plus_grace", "guided_default"}
CLAIM = {"claim": str, "status": CLAIM_STATUSES, "evidence": str}
NONEMPTY = Rule(lambda v: type(v) is str and bool(v), "a non-empty string")
NONBLANK = Rule(lambda v: type(v) is str and bool(v.strip()), "a non-empty string")
def finite_number(value: Any) -> bool:
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


NUMBER = Rule(finite_number, "a finite number")
VERSION = Rule(lambda v: v == "2.0", "2.0; legacy artifacts are not supported")
DIGEST = Rule(lambda v: type(v) is str and re.fullmatch(r"[0-9a-f]{64}", v) is not None,
              "a lowercase SHA-256 digest")
RUN_ID = Rule(lambda v: type(v) is str and v not in {".", ".."}
              and re.fullmatch(r"[A-Za-z0-9._-]+", v) is not None, "a safe non-empty basename")


def integer(minimum: int = 0, maximum: int | None = None) -> Rule:
    return Rule(lambda v: type(v) is int and v >= minimum and (maximum is None or v <= maximum),
                f"an integer from {minimum}" + (f" through {maximum}" if maximum is not None else " upward"))


def number(minimum: float, maximum: float) -> Rule:
    return Rule(lambda v: NUMBER.accepts(v) and minimum <= v <= maximum,
                f"a finite number between {minimum} and {maximum}")


def check(value: Any, spec: Any, label: str, error: type[ValueError] = ValueError) -> None:
    if isinstance(spec, dict):
        record(value, spec, label, error=error)
        return
    if isinstance(spec, list):
        if type(value) is not list:
            raise error(f"{label} must be an array")
        for index, item in enumerate(value):
            check(item, spec[0], f"{label}[{index}]", error)
        return
    if isinstance(spec, tuple):
        for alternative in spec:
            try:
                check(value, alternative, label, error)
                return
            except error:
                pass
        raise error(f"{label} does not match any allowed type or value")
    if isinstance(spec, set):
        valid = any(type(value) is type(choice) and value == choice for choice in spec)
        description = f"one of {sorted(spec, key=repr)!r}"
    elif isinstance(spec, Rule):
        valid, description = spec.accepts(value), spec.description
    elif isinstance(spec, type):
        valid = type(value) is spec
        description = {str: "a string", bool: "a boolean", int: "an integer", dict: "an object",
                       list: "an array", type(None): "null"}.get(spec, spec.__name__)
    else:
        raise TypeError(f"unsupported validation rule for {label}: {spec!r}")
    if not valid:
        raise error(f"{label} must be {description}")


def record(value: Any, required: dict[str, Any], label: str, *,
           optional: dict[str, Any] | None = None, error: type[ValueError] = ValueError,
           allow_extra: bool = False) -> None:
    if type(value) is not dict:
        raise error(f"{label} must be an object")
    fields = {**required, **(optional or {})}
    missing, unknown = set(required) - value.keys(), value.keys() - fields.keys()
    if missing:
        raise error(f"{label} missing required keys: {sorted(missing)}")
    if unknown and not allow_extra:
        raise error(f"{label} has unknown keys: {sorted(unknown)}")
    for name, spec in fields.items():
        if name in value:
            check(value[name], spec, f"{label} {name}", error)
