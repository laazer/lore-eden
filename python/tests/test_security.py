"""Password hashing, secret encryption, and JWTs.

The password tests carry a measurement, not a claim: `bridgepath`'s
timing-attack defence used a malformed dummy hash, so the verify raised
immediately instead of spending time. Measured at 0.0 ms against 356 ms for a
real verify — about 22,000×. The `except Exception: pass` around it hid the
raise, so the code read as defended.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta

import pytest
from lore_eden.security import (
    ACCESS,
    REFRESH,
    PasswordHashError,
    SecretEncryptionError,
    TokenError,
    TokenPolicy,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    generate_key,
    hash_password,
    issue_token,
    jti_of,
    needs_rehash,
    rotate_secret,
    spend_verification_time,
    subject_of,
    verify_password,
)

# Cheap cost for the round-trip tests. The timing tests use the real default,
# because a cost of 4 would make the measurement meaningless.
FAST = 4


class TestPasswords:
    def test_a_password_verifies_against_its_own_hash(self) -> None:
        hashed = hash_password("correct horse", rounds=FAST)
        assert verify_password("correct horse", hashed)
        assert not verify_password("Correct Horse", hashed)

    def test_the_same_password_hashes_differently_each_time(self) -> None:
        # Per-call salt. Two identical hashes would mean two users with the
        # same password are visibly the same in the table.
        first = hash_password("same", rounds=FAST)
        second = hash_password("same", rounds=FAST)
        assert first != second
        assert verify_password("same", first) and verify_password("same", second)

    def test_a_stored_value_that_is_not_a_hash_raises(self) -> None:
        # Not False. "Wrong password" and "this column holds junk" are
        # different problems, and conflating them turns a broken migration into
        # a support ticket about one user who cannot log in.
        with pytest.raises(PasswordHashError):
            verify_password("anything", "not-a-bcrypt-hash")

    def test_the_exact_malformed_hash_bridgepath_used_raises(self) -> None:
        # 63 characters; a bcrypt hash is 60.
        bad = "$2b$12$00000000000000000000000000000000000000000000000000000000"
        assert len(bad) == 63
        with pytest.raises(PasswordHashError):
            verify_password("dummy", bad)

    def test_needs_rehash_sees_a_weaker_stored_cost(self) -> None:
        assert needs_rehash(hash_password("x", rounds=FAST), rounds=12)
        assert not needs_rehash(hash_password("x", rounds=FAST), rounds=FAST)

    def test_needs_rehash_raises_on_something_it_cannot_read(self) -> None:
        with pytest.raises(PasswordHashError):
            needs_rehash("garbage")


class TestTheTimingDefenceActuallySpendsTime:
    """The defect this module was written around."""

    def test_the_absent_user_path_costs_real_work(self) -> None:
        started = time.perf_counter()
        spend_verification_time()
        elapsed = time.perf_counter() - started
        # A malformed dummy hash raises in microseconds. Anything under a
        # millisecond means no hashing happened, which is the bug.
        assert elapsed > 0.001, f"spent only {elapsed * 1000:.3f}ms; it is not hashing"

    def test_it_costs_about_what_a_real_verify_costs(self) -> None:
        # The property that matters: an attacker timing the login cannot tell
        # a missing account from a wrong password.
        hashed = hash_password("secret")

        started = time.perf_counter()
        verify_password("wrong", hashed)
        real = time.perf_counter() - started

        started = time.perf_counter()
        spend_verification_time()
        dummy = time.perf_counter() - started

        ratio = max(real, dummy) / max(min(real, dummy), 1e-9)
        assert ratio < 3, f"paths differ by {ratio:.0f}x; the timing signal is still there"

    def test_it_returns_nothing_to_branch_on(self) -> None:
        # A caller that branched on a result would reintroduce the leak.
        assert spend_verification_time() is None


class TestSecretsAtRest:
    def test_a_secret_round_trips(self) -> None:
        key = generate_key()
        assert decrypt_secret(encrypt_secret("plaid-token", key), key) == "plaid-token"

    def test_the_ciphertext_does_not_contain_the_secret(self) -> None:
        key = generate_key()
        assert "plaid-token" not in encrypt_secret("plaid-token", key)

    def test_encrypting_twice_gives_different_ciphertext(self) -> None:
        # Fernet includes a timestamp and IV, so equal secrets are not equal
        # ciphertexts — two rows holding the same token are not visibly equal.
        key = generate_key()
        assert encrypt_secret("same", key) != encrypt_secret("same", key)

    def test_the_wrong_key_raises_rather_than_returning_none(self) -> None:
        # None would be stored, sent or compared, and would surface as an
        # authentication failure against the third-party service instead of as
        # the key problem it is.
        cipher = encrypt_secret("token", generate_key())
        with pytest.raises(SecretEncryptionError):
            decrypt_secret(cipher, generate_key())

    def test_a_tampered_ciphertext_raises(self) -> None:
        key = generate_key()
        cipher = encrypt_secret("token", key)
        tampered = cipher[:-4] + ("AAAA" if not cipher.endswith("AAAA") else "BBBB")
        with pytest.raises(SecretEncryptionError):
            decrypt_secret(tampered, key)

    def test_the_message_does_not_say_which_failure_it_was(self) -> None:
        # Telling a wrong key from a tampered value tells an attacker which of
        # the two they achieved.
        key, other = generate_key(), generate_key()
        cipher = encrypt_secret("token", key)
        with pytest.raises(SecretEncryptionError) as wrong_key:
            decrypt_secret(cipher, other)
        assert "wrong key or altered value" in str(wrong_key.value)

    def test_rotation_re_encrypts_under_a_new_key(self) -> None:
        old, new = generate_key(), generate_key()
        cipher = encrypt_secret("token", old)
        rotated = rotate_secret(cipher, old, new)
        assert decrypt_secret(rotated, new) == "token"
        with pytest.raises(SecretEncryptionError):
            decrypt_secret(rotated, old)


class TestTokens:
    POLICY = TokenPolicy(secret="a" * 40)  # long enough for HS256; see MIN_HMAC_SECRET_BYTES

    def test_a_token_round_trips(self) -> None:
        issued = issue_token("user-1", self.POLICY)
        assert subject_of(issued.token, self.POLICY) == "user-1"

    def test_there_is_no_default_secret(self) -> None:
        # A library shipping a default signing key ships a forgery kit.
        with pytest.raises(ValueError, match="no default"):
            TokenPolicy(secret="")

    def test_a_short_hmac_secret_is_refused_not_merely_warned_about(self) -> None:
        # PyJWT warns below 32 bytes and signs anyway. A warning in a log
        # nobody greps is how a 12-character secret reaches production.
        with pytest.raises(ValueError, match="at least 32 bytes"):
            TokenPolicy(secret="too-short")

    def test_the_message_says_how_to_make_a_good_one(self) -> None:
        with pytest.raises(ValueError) as caught:
            TokenPolicy(secret="short")
        assert "token_urlsafe" in str(caught.value)

    def test_an_asymmetric_algorithm_is_exempt_from_the_length_floor(self) -> None:
        # An RS256 key is a PEM document; its strength is unrelated to its
        # character count.
        assert TokenPolicy(secret="-----BEGIN KEY-----", algorithm="RS256")

    def test_a_token_signed_with_another_secret_is_refused(self) -> None:
        issued = issue_token("user-1", TokenPolicy(secret="b" * 40))
        with pytest.raises(TokenError):
            decode_token(issued.token, self.POLICY)

    def test_an_expired_token_is_refused(self) -> None:
        issued = issue_token("user-1", self.POLICY, lifetime=timedelta(seconds=-60))
        with pytest.raises(TokenError):
            decode_token(issued.token, self.POLICY)

    def test_the_wrong_kind_is_refused_even_with_a_valid_signature(self) -> None:
        # The check most often left out. Without it a long-lived refresh token
        # works as an access token everywhere.
        refresh = issue_token("user-1", self.POLICY, kind=REFRESH)
        with pytest.raises(TokenError, match="Expected a access token"):
            decode_token(refresh.token, self.POLICY, expect=ACCESS)

    def test_extra_claims_cannot_overwrite_the_registered_ones(self) -> None:
        # Otherwise a caller passing exp would silently extend the lifetime.
        issued = issue_token(
            "user-1", self.POLICY, claims={"exp": 99999999999, "sub": "someone-else", "role": "x"}
        )
        payload = decode_token(issued.token, self.POLICY)
        assert payload["sub"] == "user-1"
        assert payload["role"] == "x"
        assert payload["exp"] != 99999999999

    def test_a_jti_is_issued_so_a_host_can_revoke(self) -> None:
        issued = issue_token("user-1", self.POLICY, kind=REFRESH)
        assert jti_of(issued.token, self.POLICY) == issued.jti
        assert isinstance(issued.jti, uuid.UUID)  # py-org: allow-isinstance

    def test_two_tokens_have_different_jtis(self) -> None:
        first = issue_token("user-1", self.POLICY)
        second = issue_token("user-1", self.POLICY)
        assert first.jti != second.jti

    def test_issuer_and_audience_are_checked_when_set(self) -> None:
        strict = TokenPolicy(secret="c" * 40, issuer="lore-eden", audience="api")
        issued = issue_token("user-1", strict)
        assert subject_of(issued.token, strict) == "user-1"
        other = TokenPolicy(secret="c" * 40, issuer="lore-eden", audience="different")
        with pytest.raises(TokenError):
            decode_token(issued.token, other)

    def test_clock_skew_is_tolerated(self) -> None:
        # A token rejected a second early is an intermittent logout nobody can
        # reproduce.
        issued = issue_token("user-1", self.POLICY, lifetime=timedelta(seconds=-5))
        assert subject_of(issued.token, self.POLICY) == "user-1"

    def test_refresh_lifetime_is_longer_than_access_by_default(self) -> None:
        access = issue_token("u", self.POLICY, kind=ACCESS)
        refresh = issue_token("u", self.POLICY, kind=REFRESH)
        assert refresh.expires_at > access.expires_at
