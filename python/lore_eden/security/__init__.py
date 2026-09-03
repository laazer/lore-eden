"""Security utilities two live backends each needed and each got partly wrong.

Optional: `pip install "lore-eden[security]"`. Nothing in the harness, the
runner, the workflow engine or the store imports this.

- :mod:`~lore_eden.security.passwords` — bcrypt hashing, and a timing defence
  that spends real time. `bridgepath`'s spent none: its dummy hash was
  malformed, so the verify raised in 0.0 ms against 356 ms for a real one, and
  an `except Exception: pass` hid the raise.
- :mod:`~lore_eden.security.secrets_at_rest` — Fernet encryption for somebody
  else's token before it goes in a column. Generic code that `corpocoin` had
  named after the one secret it held.
- :mod:`~lore_eden.security.tokens` — JWTs, with the signing secret passed in
  rather than read off an application's settings at import.
"""

from lore_eden.security.passwords import (
    DEFAULT_ROUNDS,
    PasswordHashError,
    hash_password,
    needs_rehash,
    spend_verification_time,
    verify_password,
)
from lore_eden.security.secrets_at_rest import (
    SecretEncryptionError,
    decrypt_secret,
    encrypt_secret,
    generate_key,
    rotate_secret,
)
from lore_eden.security.tokens import (
    ACCESS,
    MIN_HMAC_SECRET_BYTES,
    REFRESH,
    IssuedToken,
    TokenError,
    TokenKind,
    TokenPolicy,
    decode_token,
    issue_token,
    jti_of,
    subject_of,
)

__all__ = [
    "ACCESS",
    "DEFAULT_ROUNDS",
    "MIN_HMAC_SECRET_BYTES",
    "REFRESH",
    "IssuedToken",
    "PasswordHashError",
    "SecretEncryptionError",
    "TokenError",
    "TokenKind",
    "TokenPolicy",
    "decode_token",
    "decrypt_secret",
    "encrypt_secret",
    "generate_key",
    "hash_password",
    "issue_token",
    "jti_of",
    "needs_rehash",
    "rotate_secret",
    "spend_verification_time",
    "subject_of",
    "verify_password",
]
