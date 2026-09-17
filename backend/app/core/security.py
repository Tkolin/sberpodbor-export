from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── admin passwords ─────────────────────────────────────────────────────────
def hash_password(raw: str) -> str:
    # bcrypt silently truncates at 72 bytes; pre-hash so long passphrases stay distinct.
    return pwd_context.hash(_bcrypt_safe(raw))


def verify_password(raw: str, hashed: str) -> bool:
    return pwd_context.verify(_bcrypt_safe(raw), hashed)


def _bcrypt_safe(raw: str) -> str:
    return base64.b64encode(hashlib.sha256(raw.encode()).digest()).decode()


# ── session tokens ──────────────────────────────────────────────────────────
def create_token(*, sub: str, role: str, epoch: int, kind: str = "access") -> str:
    ttl = (
        timedelta(minutes=settings.access_token_ttl_min)
        if kind == "access"
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "role": role,
        "epoch": epoch,
        "kind": kind,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# ── ATS credentials at rest ─────────────────────────────────────────────────
class SecretBox:
    """Fernet wrapper for ATS account passwords and proxy URLs.

    The key lives only in SP_ENCRYPTION_KEY. Losing it makes stored credentials
    unrecoverable — that is the point, but it means the key needs its own backup.
    """

    def __init__(self, key: str) -> None:
        if not key:
            raise RuntimeError("SP_ENCRYPTION_KEY is not set — refusing to store credentials in the clear")
        self._f = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plain: str | None) -> str | None:
        if plain is None or plain == "":
            return None
        return self._f.encrypt(plain.encode()).decode()

    def decrypt(self, blob: str | None) -> str | None:
        if not blob:
            return None
        try:
            return self._f.decrypt(blob.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError(
                "Cannot decrypt a stored credential — SP_ENCRYPTION_KEY does not match the one "
                "used to write it. Restore the original key or re-enter the account credentials."
            ) from exc


_box: SecretBox | None = None


def secret_box() -> SecretBox:
    global _box
    if _box is None:
        _box = SecretBox(settings.encryption_key)
    return _box


def mask_proxy(proxy: str | None) -> str | None:
    """http://user:pass@1.2.3.4:8000 -> http://***@1.2.3.4:8000 (safe for UI and logs)."""
    if not proxy:
        return None
    if "@" not in proxy:
        return proxy
    scheme, _, rest = proxy.partition("://")
    _creds, _, host = rest.partition("@")
    return f"{scheme}://***@{host}"
