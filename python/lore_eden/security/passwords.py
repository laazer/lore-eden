"""Password hashing, and a timing defence that actually costs time.

## The mitigation that mitigated nothing

`bridgepath` guards its login against user-enumeration by timing: when the email
matches nobody, it verifies a dummy hash so the absent-user path costs the same
as the present-user path. The idea is right. The implementation did no work::

    try:
        _verify_password_hash("dummy", "$2b$12$0000000000000000000000000000000000000000000000000000")
    except Exception:
        pass

That literal is **63 characters**; a bcrypt hash is 60. So `checkpw` rejects it
as malformed and raises immediately — measured here at **0.0 ms against 356 ms**
for a real verify, a factor of about 22,000. The `except Exception: pass` then
hides the raise, so the code reads as defended while the timing signal it exists
to remove is entirely intact.

Two lessons, both encoded below: the dummy hash is **generated**, not written
out as a literal nobody can eyeball; and nothing here swallows an exception, so
a hash this module cannot verify is a loud failure rather than a fast one.
"""

from __future__ import annotations

import bcrypt

#: Cost factor. 12 is bcrypt's common default and ~350 ms on a 2026 laptop —
#: slow enough to hurt an offline attacker, fast enough for a login. Raise it as
#: hardware improves; existing hashes keep their own cost, since bcrypt stores
#: it in the digest.
DEFAULT_ROUNDS = 12

#: A real hash of a value nobody uses, verified against on the absent-user path.
#: Generated at import so it cannot be a malformed literal — the whole defect
#: this module was written around. One hash per process is enough: it is never
#: compared against anything a caller supplied.
_DUMMY_HASH = bcrypt.hashpw(b"lore-eden-absent-user", bcrypt.gensalt(rounds=DEFAULT_ROUNDS))


class PasswordHashError(ValueError):
    """A hash this module was asked to verify is not a bcrypt hash.

    Raised rather than returning False. False means "wrong password", and a
    stored value that is not a hash at all is a different problem — a truncated
    column, a half-finished migration — that answering "wrong password" would
    turn into a support ticket about a user who cannot log in.
    """


def hash_password(password: str, *, rounds: int = DEFAULT_ROUNDS) -> str:
    """Hash a password. The salt is generated per call, as bcrypt intends."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Whether the password matches. Raises on a hash that is not one."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError as exc:
        raise PasswordHashError(f"Not a usable bcrypt hash: {exc}") from exc


def spend_verification_time() -> None:
    """Do the work a real verify would, and discard the answer.

    Call this on the path where no user matched, *before* reporting invalid
    credentials, so the response time does not reveal whether the account
    exists.

    It genuinely hashes — that is the point, and the reason the source's version
    failed. The return value is deliberately dropped: there is nothing to
    compare and a caller that branched on it would be reintroducing the leak.
    """
    bcrypt.checkpw(b"lore-eden-absent-user-probe", _DUMMY_HASH)


def needs_rehash(hashed: str, *, rounds: int = DEFAULT_ROUNDS) -> bool:
    """Whether a stored hash is weaker than current policy.

    Lets a host upgrade cost silently at next login, which is the only moment
    it has the plaintext. Without this, raising `DEFAULT_ROUNDS` protects new
    accounts and leaves every existing one at the old cost forever.
    """
    try:
        stored = int(hashed.split("$")[2])
    except (IndexError, ValueError) as exc:
        raise PasswordHashError(f"Cannot read the cost factor from: {hashed[:16]}…") from exc
    return stored < rounds
