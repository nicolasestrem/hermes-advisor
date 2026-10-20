"""Pure-Python core for the Hermes Advisor plugin adapter."""

from .config import ConfigError, default_settings, validate_settings
from .packet import CapturedPacket, PacketError, build_packet, sanitize_text
from .service import (
    ConfigurationConflict,
    OwnerUnavailable,
    ReviewerResult,
    Service,
    ServiceError,
    SessionNotDiscovered,
)
from .store import SQLiteStore, StoreError
from .response import ResponseValidationError, validate_response

__all__ = [
    "CapturedPacket",
    "ConfigError",
    "ConfigurationConflict",
    "OwnerUnavailable",
    "PacketError",
    "ResponseValidationError",
    "ReviewerResult",
    "SQLiteStore",
    "Service",
    "ServiceError",
    "SessionNotDiscovered",
    "StoreError",
    "build_packet",
    "default_settings",
    "sanitize_text",
    "validate_response",
    "validate_settings",
]
