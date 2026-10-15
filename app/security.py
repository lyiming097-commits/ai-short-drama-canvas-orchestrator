from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class CredentialVault:
    """Encrypts provider credentials before they enter the repository.

    Production must provide a stable MODEL_CONFIG_ENCRYPTION_KEY backed by a
    secret manager. The development fallback is intentionally process-local
    and should never be used for real provider credentials.
    """

    def __init__(self, key: str | None = None, *, development: bool = True) -> None:
        if not key:
            if not development:
                raise RuntimeError("MODEL_CONFIG_ENCRYPTION_KEY is required in production")
            digest = hashlib.sha256(b"short-drama-studio-development-only").digest()
            key = base64.urlsafe_b64encode(digest).decode()
        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("MODEL_CONFIG_ENCRYPTION_KEY must be a Fernet key") from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("stored provider credential cannot be decrypted") from exc

    @staticmethod
    def mask(value: str) -> str:
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * max(4, len(value) - 8)}{value[-4:]}"

