"""Pillar 2 — Security: Secret lifecycle management and safe logging. Satisfies SOX IT General Controls."""

import os
from datetime import datetime, timezone
from typing import Optional

import structlog

_SENSITIVE_KEY_FRAGMENTS = ("key", "secret", "password", "token", "credential", "auth")

_REQUIRED_SECRETS = ("DATABASE_URL", "API_SECRET_KEY")
_OPTIONAL_SECRETS = (
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "JINA_API_KEY",
    "FRED_API_KEY",
)

_ROTATION_MAX_DAYS = 90


# ── Masking ───────────────────────────────────────────────────────────────────

def mask_secret(value: Optional[str]) -> str:
    """Return a masked representation of a secret, showing only the last 4 chars."""
    if not value or len(value) < 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


# ── Rotation Alerts ───────────────────────────────────────────────────────────

class SecretRotationAlert:
    """Check environment variables whose names end in _CREATED_AT for rotation age."""

    def check_rotation_needed(self) -> list[str]:
        """
        Scan environment for *_CREATED_AT vars, parse their ISO dates,
        and return names of secrets older than ROTATION_MAX_DAYS days.
        Emits structlog WARNING for each overdue secret.
        """
        _log = structlog.get_logger().bind(pillar="security", component="SecretRotationAlert")
        overdue: list[str] = []

        for env_name, env_value in os.environ.items():
            if not env_name.endswith("_CREATED_AT"):
                continue
            secret_name = env_name[: -len("_CREATED_AT")]
            try:
                created_at = datetime.fromisoformat(env_value)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(tz=timezone.utc) - created_at).days
                if age_days > _ROTATION_MAX_DAYS:
                    _log.warning(
                        "secret_rotation_overdue",
                        secret_name=secret_name,
                        age_days=age_days,
                        max_days=_ROTATION_MAX_DAYS,
                    )
                    overdue.append(secret_name)
            except (ValueError, TypeError):
                _log.warning(
                    "secret_created_at_unparseable",
                    env_var=env_name,
                    raw_value=env_value,
                )

        return overdue


# ── Safe Logger ───────────────────────────────────────────────────────────────

class SafeLogger:
    """Structlog wrapper that automatically masks secret-like kwargs."""

    def __init__(self, name: str) -> None:
        self._logger = structlog.get_logger(name).bind(pillar="security")

    def _sanitize(self, kwargs: dict) -> dict:
        sanitized: dict = {}
        for k, v in kwargs.items():
            lower_key = k.lower()
            if any(fragment in lower_key for fragment in _SENSITIVE_KEY_FRAGMENTS):
                sanitized[k] = mask_secret(str(v)) if v is not None else "****"
            else:
                sanitized[k] = v
        return sanitized

    def info(self, msg: str, **kwargs) -> None:
        self._logger.info(msg, **self._sanitize(kwargs))

    def warning(self, msg: str, **kwargs) -> None:
        self._logger.warning(msg, **self._sanitize(kwargs))

    def error(self, msg: str, **kwargs) -> None:
        self._logger.error(msg, **self._sanitize(kwargs))


# ── Secret Manager ────────────────────────────────────────────────────────────

class SecretManager:
    """Centralised secret loader with validation and masked inspection."""

    _singleton: Optional["SecretManager"] = None

    def __init__(self) -> None:
        self._secrets: dict[str, Optional[str]] = {}
        for name in _REQUIRED_SECRETS:
            self._secrets[name] = os.getenv(name)
        for name in _OPTIONAL_SECRETS:
            self._secrets[name] = os.getenv(name)

    def validate(self) -> list[str]:
        """Return names of required secrets that are missing or empty."""
        return [
            name
            for name in _REQUIRED_SECRETS
            if not self._secrets.get(name)
        ]

    def get(self, name: str) -> Optional[str]:
        return self._secrets.get(name) or os.getenv(name)

    def get_masked(self, name: str) -> str:
        return mask_secret(self.get(name))

    @classmethod
    def instance(cls) -> "SecretManager":
        if cls._singleton is None:
            cls._singleton = cls()
        return cls._singleton


# ── Module-level safe logger ──────────────────────────────────────────────────

safe_logger = SafeLogger(__name__)

# Run rotation check on import; log warnings for any overdue secrets
_overdue = SecretRotationAlert().check_rotation_needed()
if _overdue:
    safe_logger.warning(
        "secrets_pending_rotation",
        overdue_count=len(_overdue),
        secrets=", ".join(_overdue),
    )
