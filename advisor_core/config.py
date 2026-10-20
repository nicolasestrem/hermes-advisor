"""Strict local validation for reviewer configuration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .packet import sanitize_text


class ConfigError(ValueError):
    """Raised when configuration is not an exact, supported JSON shape."""


DEFAULTS: dict[str, Any] = {
    "packet_bytes": 32768,
    "output_tokens": 2048,
    "max_findings": 4,
    "deadline_seconds": 120,
    "session_allowance": 20,
}

_LIMITS: dict[str, tuple[int, int]] = {
    "packet_bytes": (1024, 32768),
    "output_tokens": (128, 2048),
    "max_findings": (1, 4),
    "deadline_seconds": (1, 120),
    "session_allowance": (1, 20),
}
_ALLOWED_KEYS = frozenset((*DEFAULTS, "route"))


def validate_settings(
    settings: Mapping[str, Any],
    *,
    route_validator: Callable[[dict[str, str]], Any] | None = None,
    require_route: bool = True,
) -> dict[str, Any]:
    """Return a normalized complete settings dict without coercion.

    ``require_route=False`` is reserved for the initial unconfigured profile
    state. A saved-and-applied configuration must always have a complete route.
    The optional callback is the host adapter's public route/grant validator;
    this pure core never resolves models or credentials itself.
    """
    if not isinstance(settings, Mapping):
        raise ConfigError("settings must be an object")
    unknown = set(settings) - _ALLOWED_KEYS
    if unknown:
        raise ConfigError("unknown setting key")

    normalized: dict[str, Any] = dict(DEFAULTS)
    route = settings.get("route")
    if route is None:
        if require_route:
            raise ConfigError("an explicit reviewer route is required")
        normalized["route"] = None
    else:
        if not isinstance(route, Mapping) or set(route) != {"provider", "model"}:
            raise ConfigError("route must contain exactly provider and model")
        clean_route: dict[str, str] = {}
        for key in ("provider", "model"):
            value = route[key]
            if (
                not isinstance(value, str) or not value or value != value.strip() or len(value) > 256
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ConfigError(f"route {key} must be a trimmed printable nonempty string of at most 256 code points")
            if sanitize_text(value, max_chars=len(value)) != value:
                raise ConfigError(f"route {key} must not contain credential patterns")
            clean_route[key] = value
        if route_validator is not None:
            try:
                valid = route_validator(dict(clean_route))
            except Exception as exc:
                raise ConfigError("host route validation failed") from exc
            if valid is False:
                raise ConfigError("host rejected the reviewer route")
        normalized["route"] = clean_route

    for key, bounds in _LIMITS.items():
        if key in settings:
            value = settings[key]
            low, high = bounds
            if type(value) is not int or not low <= value <= high:
                raise ConfigError(f"{key} must be an integer from {low} through {high}")
            normalized[key] = value
    return normalized


def default_settings() -> dict[str, Any]:
    """Return a fresh, unconfigured settings envelope."""
    return {**DEFAULTS, "route": None}
