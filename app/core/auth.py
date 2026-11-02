"""API token primitives.

Token format: ``tw_demo_`` followed by 32 lowercase alphanumerics, for example
``tw_demo_4k2m9x0q...``. The ``demo`` marker is deliberate: every token this
codebase can mint is a non-production DEMO credential.

Storage: we keep ``prefix`` (the first :data:`PREFIX_LENGTH` characters, used
to narrow the lookup), a random per-token ``salt`` and
``sha256(salt + token)`` as hex. The plaintext is shown once, at issue time.

Request-time lookup (find rows by prefix, verify, check revocation, scope to
the tenant) lives in ``app.api.deps``; this module has no FastAPI imports so
the CLI and seed can use it too.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import Unauthorized
from app.models.tenant import ApiToken

TOKEN_PREFIX = "tw_demo_"  # noqa: S105 - a prefix, not a secret
TOKEN_BODY_LENGTH = 32
PREFIX_LENGTH = 12  # "tw_demo_" + 4 body characters
_ALPHABET = string.ascii_lowercase + string.digits
_TOKEN_RE = re.compile(rf"^{TOKEN_PREFIX}[a-z0-9]{{{TOKEN_BODY_LENGTH}}}$")


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A freshly generated token. ``plaintext`` must never be stored or logged."""

    plaintext: str
    prefix: str
    salt: str
    token_hash: str

    def __repr__(self) -> str:  # keep the plaintext out of tracebacks and logs
        return f"IssuedToken(prefix={self.prefix!r})"


def generate_token() -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(TOKEN_BODY_LENGTH))
    return TOKEN_PREFIX + body


def looks_like_token(value: str) -> bool:
    return bool(_TOKEN_RE.match(value))


def token_prefix(token: str) -> str:
    return token[:PREFIX_LENGTH]


def new_salt() -> str:
    return secrets.token_hex(16)


def hash_token(token: str, salt: str) -> str:
    return hashlib.sha256((salt + token).encode("utf-8")).hexdigest()


def verify_token(token: str, salt: str, expected_hash: str) -> bool:
    """Constant-time comparison of ``hash_token(token, salt)`` with the stored hash."""
    return hmac.compare_digest(hash_token(token, salt), expected_hash)


def issue_token() -> IssuedToken:
    token = generate_token()
    salt = new_salt()
    return IssuedToken(
        plaintext=token, prefix=token_prefix(token), salt=salt, token_hash=hash_token(token, salt)
    )


def create_api_token(session: Session, tenant_id: int, label: str = "") -> tuple[ApiToken, str]:
    """Insert a new token row for ``tenant_id`` and return it with the plaintext.

    Flushes (so ``row.id`` is set) but does not commit.
    """
    issued = issue_token()
    row = ApiToken(
        tenant_id=tenant_id,
        prefix=issued.prefix,
        salt=issued.salt,
        token_hash=issued.token_hash,
        label=label,
    )
    session.add(row)
    session.flush()
    return row, issued.plaintext


def revoke_api_token(session: Session, row: ApiToken) -> None:
    if row.revoked_at is None:
        row.revoked_at = clock.now()
        session.flush()


def parse_bearer(header_value: str | None) -> str:
    """Extract the token from an ``Authorization: Bearer <token>`` header value.

    Raises :class:`Unauthorized` for a missing header, another scheme or a value
    that is not shaped like a Tidewright token (checked before any DB access).
    """
    if not header_value:
        raise Unauthorized("missing bearer token")
    scheme, _, value = header_value.strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise Unauthorized("expected 'Authorization: Bearer <token>'")
    token = value.strip()
    if not token.startswith(TOKEN_PREFIX):
        raise Unauthorized("malformed token")
    return token


def constant_time_equals(a: str, b: str) -> bool:
    """For comparing configured secrets such as TW_ADMIN_TOKEN."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
