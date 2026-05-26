"""Session-cookie auth backed by an argon2 password hash.

The hash is supplied via a Docker secret file (see ``AIO_PASSWORD_FILE``). A
plain-text password is also accepted (useful for local dev) — we detect which
mode we're in by checking the ``$argon2`` prefix on the stored value.

The session cookie itself is signed by ``starlette.middleware.sessions`` using
the secret returned by :meth:`Env.resolve_session_secret`.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Request

from .config import env

# One ``PasswordHasher`` instance — argon2-cffi recommends reusing it because
# it caches the underlying native context.
_ph = PasswordHasher()


def is_hash(s: str) -> bool:
    """Cheap discriminator: argon2-cffi always emits hashes that start with
    ``$argon2``. Anything else we treat as plain text (dev mode only)."""
    return s.startswith("$argon2")


def hash_password(plain: str) -> str:
    """Return an argon2 hash. Used by ``tools/hash_password.py``."""
    return _ph.hash(plain)


def verify(plain_attempt: str) -> bool:
    """Constant-time-ish verify against the stored secret.

    argon2's verify is constant-time over the hash bytes; the plain-text
    fallback compares with ``==`` and is *not* constant-time, but is only
    intended for local development.
    """
    stored = env().resolve_password()
    if not stored:
        return False
    if is_hash(stored):
        try:
            _ph.verify(stored, plain_attempt)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False
    return plain_attempt == stored


def is_authenticated(request: Request) -> bool:
    """Truthy when the session cookie has a ``user`` set by :func:`login`."""
    return bool(request.session.get("user"))


def login(request: Request, username: str) -> None:
    request.session["user"] = username


def logout(request: Request) -> None:
    request.session.pop("user", None)
