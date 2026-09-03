"""Issuing and verifying JWTs, with the secret passed in rather than imported.

From `corpocoin`'s `domains/auth/tokens.py`, which is good work with one thing
that cannot travel: it does `settings = get_settings()` at module import and
reads the secret, algorithm and expiries off it. That single line is what makes
it that application's module rather than a library — and it means importing it
in a test requires that application's settings to exist.

So the secret and the policy arrive as arguments, gathered into
:class:`TokenPolicy` so a host configures them once rather than at every call.

## PyJWT rather than python-jose

The source uses `python-jose`. This uses `PyJWT`, and the swap is safe in the
one way that matters: a JWT is a wire format, so a token issued by either
verifies with the other. Nothing about a stored or in-flight token changes.
PyJWT is the more maintained of the two and validates `exp` by default, which
`jose` also does but which is worth having from the library that says so.

## What is checked, and what a host still owes

Verification checks the signature and `exp`. It does **not** check that a token
is the *kind* the caller wanted — an access token presented where a refresh
token belongs has a valid signature — so :func:`decode_token` takes the
expected type and refuses a mismatch. That check is the one most often left
out, and leaving it out lets a long-lived refresh token be used as an access
token everywhere.

Revocation is the host's: `jti` is issued so a host can store and blacklist it.
A JWT cannot be un-issued by the library that made it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping

import jwt

class TokenKind(str, Enum):
    """What a token is for.

    An enum rather than two string constants: ``kind`` and ``expect`` are the
    parameters that decide whether a refresh token may be used as an access
    token, and a typo in a string there is a security hole a type checker
    would have caught.

    ``(str, Enum)`` rather than ``StrEnum`` — the latter is 3.11+ and this
    package installs on 3.10. Members compare equal to their value either way,
    which is what the JWT payload relies on.
    """

    ACCESS = "access"
    REFRESH = "refresh"


#: Kept as module-level names because they read better at a call site than
#: `TokenKind.ACCESS` does, and they are the same objects.
ACCESS = TokenKind.ACCESS
REFRESH = TokenKind.REFRESH

#: RFC 7518 §3.2: an HMAC key for SHA-256 should be at least as long as the
#: hash output. PyJWT warns below this; a library that only warns leaves the
#: weak key in production, so this refuses it.
MIN_HMAC_SECRET_BYTES = 32


class TokenError(Exception):
    """A token that cannot be trusted: bad signature, expired, or wrong kind."""


@dataclass(frozen=True)
class TokenPolicy:
    """The secret and the lifetimes, supplied by the host.

    ``secret`` is required, has no default, and for the HMAC algorithms must be
    at least :data:`MIN_HMAC_SECRET_BYTES` long.

    No default because a library shipping a signing key ships a forgery kit,
    and the one host that forgets to override it is the one that gets away with
    it in development. The length floor because PyJWT only *warns* below it —
    and a warning in a log nobody greps is how a 12-character secret reaches
    production. RFC 7518 §3.2 asks for a key at least as long as the hash.

    Asymmetric algorithms are exempt: an RS256 key is a PEM document whose
    strength has nothing to do with its character count.
    """

    secret: str
    algorithm: str = "HS256"
    access_lifetime: timedelta = timedelta(minutes=15)
    refresh_lifetime: timedelta = timedelta(days=30)
    #: Clock skew allowed when checking `exp`. Small and non-zero: two machines
    #: are never in perfect agreement, and a token rejected a second early is
    #: an intermittent logout nobody can reproduce.
    leeway: timedelta = timedelta(seconds=10)
    issuer: str = ""
    audience: str = ""

    def __post_init__(self) -> None:
        if not self.secret:
            raise ValueError("A signing secret is required; there is no default.")
        if self.algorithm.startswith("HS"):
            length = len(self.secret.encode("utf-8"))
            if length < MIN_HMAC_SECRET_BYTES:
                raise ValueError(
                    f"An {self.algorithm} secret must be at least "
                    f"{MIN_HMAC_SECRET_BYTES} bytes; this one is {length}. "
                    "Generate one with: python -c "
                    "\"import secrets; print(secrets.token_urlsafe(32))\""
                )


@dataclass(frozen=True)
class IssuedToken:
    """A signed token and the `jti` a host needs to be able to revoke it."""

    token: str
    jti: uuid.UUID
    expires_at: datetime
    kind: TokenKind = ACCESS


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def issue_token(
    subject: str,
    policy: TokenPolicy,
    *,
    kind: TokenKind = ACCESS,
    lifetime: timedelta | None = None,
    claims: Mapping[str, Any] | None = None,
) -> IssuedToken:
    """Sign a token for ``subject``.

    ``claims`` is merged *under* the registered ones, so a caller cannot
    overwrite `exp`, `sub`, `jti` or `type` by passing them — which would let
    an extra claim silently extend a token's life.
    """
    jti = uuid.uuid4()
    span = lifetime or (
        policy.refresh_lifetime if kind is REFRESH else policy.access_lifetime
    )
    expires_at = _now() + span

    payload: dict[str, Any] = {
        **dict(claims or {}),
        "sub": subject,
        "type": kind.value,
        "jti": str(jti),
        "iat": _now(),
        "exp": expires_at,
    }
    if policy.issuer:
        payload["iss"] = policy.issuer
    if policy.audience:
        payload["aud"] = policy.audience

    encoded = jwt.encode(payload, policy.secret, algorithm=policy.algorithm)
    return IssuedToken(token=encoded, jti=jti, expires_at=expires_at, kind=kind)


def decode_token(
    token: str, policy: TokenPolicy, *, expect: TokenKind | None = None
) -> dict[str, Any]:
    """Verify and decode. Raises :class:`TokenError` on anything untrustworthy.

    ``expect`` is the token *kind*. Pass it. A valid signature says the token
    came from this issuer, not that it is the token this endpoint wanted.
    """
    try:
        payload = jwt.decode(
            token,
            policy.secret,
            algorithms=[policy.algorithm],
            leeway=policy.leeway,
            issuer=policy.issuer or None,
            audience=policy.audience or None,
            options={"verify_aud": bool(policy.audience)},
        )
    except jwt.PyJWTError as exc:
        # Narrowed to PyJWT's own base rather than Exception: a TypeError from
        # a mis-built policy is a bug here, not an untrusted token, and
        # reporting it as one would hide it.
        raise TokenError(str(exc)) from exc

    if expect is not None and payload.get("type") != expect.value:
        raise TokenError(
            f"Expected a {expect.value} token, got {payload.get('type') or 'none'}"
        )
    return payload


def subject_of(token: str, policy: TokenPolicy, *, expect: TokenKind = ACCESS) -> str:
    """The `sub` claim, verified."""
    return str(decode_token(token, policy, expect=expect)["sub"])


def jti_of(token: str, policy: TokenPolicy, *, expect: TokenKind = REFRESH) -> uuid.UUID:
    """The `jti`, for a host checking its revocation list."""
    payload = decode_token(token, policy, expect=expect)
    try:
        return uuid.UUID(str(payload["jti"]))
    except (KeyError, ValueError) as exc:
        raise TokenError("Token carries no usable jti") from exc
