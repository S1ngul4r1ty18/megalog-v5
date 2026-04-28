"""Auth: Argon2id para senhas + JWT em HttpOnly cookie.

Substitui o esquema do v4 (SHA-256+salt em [app/models.py](../../../app/models.py))
por Argon2id (vencedor PHC, resistente a GPU/ASIC) e JWT opaco no cookie.

Migração transparente: se um hash legado (formato 'hex(salt)$hex(sha256(...))')
for verificado com sucesso, o caller deve re-hashar para Argon2 e persistir.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

JWT_ALGORITHM = "HS256"
COOKIE_NAME = "megalog_session"

# Singleton — Argon2 é caro de instanciar repetidamente
_ph = PasswordHasher()


# ── Senhas ────────────────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(stored_hash: str, plain: str) -> tuple[bool, bool]:
    """
    Retorna (válida, precisa_rehash).
    Aceita Argon2 nativo ou hash legado v4 ('hex(salt)$hex(sha256(salt+pass))').
    """
    if "$argon2" in stored_hash:
        try:
            _ph.verify(stored_hash, plain)
            needs_rehash = _ph.check_needs_rehash(stored_hash)
            return True, needs_rehash
        except VerifyMismatchError:
            return False, False
        except Exception:
            return False, False
    # Fallback: hash legado v4
    if "$" in stored_hash:
        try:
            salt_hex, expected_hex = stored_hash.split("$", 1)
            salt = bytes.fromhex(salt_hex)
            actual = hashlib.sha256(salt + plain.encode("utf-8")).hexdigest()
            if secrets.compare_digest(actual, expected_hex):
                return True, True  # sempre re-hashar para Argon2
            return False, False
        except (ValueError, TypeError):
            return False, False
    return False, False


# ── JWT ───────────────────────────────────────────────────────────────────────


def issue_token(
    *,
    user_id: int,
    username: str,
    role: str,
    secret: str,
    ttl_minutes: int,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_token(token: str, *, secret: str) -> dict | None:
    try:
        return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
