"""Encrypting a third-party secret before it goes in a column.

Lifted from `corpocoin`, where it is named for the one secret it happened to
hold — `encrypt_access_token`, docstrings about Plaid. The **code** is entirely
generic: value in, key in, ciphertext out, with no import from the application
and nothing Plaid-shaped in it. Only the naming made it look like domain code,
which is why it survived a first pass that read filenames.

Any application storing a token, a webhook secret or an API key for someone
else's service needs exactly this, so the names are the general ones.

## Fernet, and what it does not solve

Fernet is authenticated symmetric encryption — a tampered ciphertext fails to
decrypt rather than decrypting to something else. What it cannot do is protect
you from losing the key, or from keeping the key next to the database. The key
belongs in whatever the host uses for secrets; a constant in the settings module
means the ciphertext and its key are in the same backup.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretEncryptionError(Exception):
    """A secret could not be decrypted.

    Raised rather than returning None, and this is the source's choice worth
    keeping: a caller that got None would store it, or send it, or compare it —
    and a wrong key would present as an authentication failure against the
    third-party service rather than as the key problem it is.
    """


def generate_key() -> str:
    """A new URL-safe base64 32-byte key, for a host to put in its secret store."""
    return Fernet.generate_key().decode()


def encrypt_secret(value: str, key: str) -> str:
    """Encrypt a secret for storage. The ciphertext is safe to put in a column."""
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def decrypt_secret(ciphertext: str, key: str) -> str:
    """Decrypt a stored secret. Raises when the key is wrong or the value tampered."""
    try:
        return Fernet(key.encode()).decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        # One message for a wrong key and for a tampered value, deliberately:
        # telling them apart tells an attacker which of the two they achieved.
        raise SecretEncryptionError("Could not decrypt: wrong key or altered value") from exc


def rotate_secret(ciphertext: str, old_key: str, new_key: str) -> str:
    """Re-encrypt under a new key, without the plaintext leaving this call.

    Key rotation is the operation a host will need and the one it will
    otherwise write by hand — usually as decrypt-then-encrypt with the
    plaintext sitting in a local variable through a database round trip.
    """
    return encrypt_secret(decrypt_secret(ciphertext, old_key), new_key)
