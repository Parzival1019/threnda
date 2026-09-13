"""Unit tests for threnda.app.services.auth — passwords, sessions, login."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from threnda.app.models.user import User
from threnda.app.services import auth


def _make_user(
    db_session: Session, username: str = "alice", password: str = "Correct-Horse1"
) -> User:
    user = User(username=username, password_hash=auth.hash_password(password))
    db_session.add(user)
    db_session.flush()
    return user


class TestUsernameValidation:
    def test_normalize_trims_and_lowercases(self) -> None:
        assert auth.normalize_username("  Alice.Bob  ") == "alice.bob"

    @pytest.mark.parametrize("raw", ["ab", "a" * 65, "bad user", "bad$user", "UPPER!", ""])
    def test_rejects_invalid_usernames(self, raw: str) -> None:
        with pytest.raises(auth.InvalidUsernameError):
            auth.validate_username(raw)

    @pytest.mark.parametrize("raw", ["abc", "a" * 64, "a.b-c_d", "user123"])
    def test_accepts_valid_usernames(self, raw: str) -> None:
        assert auth.validate_username(raw) == raw.strip().lower()


class TestPasswordPolicy:
    def test_rejects_too_short(self) -> None:
        with pytest.raises(auth.WeakPasswordError):
            auth.validate_password_strength("Sh0rt!", username="alice")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(auth.WeakPasswordError):
            auth.validate_password_strength("Aa1!" * 100, username="alice")

    def test_rejects_same_as_username(self) -> None:
        with pytest.raises(auth.WeakPasswordError):
            auth.validate_password_strength("Alice12345678", username="Alice12345678")

    def test_rejects_common_password(self) -> None:
        with pytest.raises(auth.WeakPasswordError):
            auth.validate_password_strength("password123", username="alice")

    def test_rejects_single_character_class(self) -> None:
        with pytest.raises(auth.WeakPasswordError):
            auth.validate_password_strength("alllowercaseletters", username="alice")

    def test_accepts_a_reasonable_password(self) -> None:
        auth.validate_password_strength("Correct-Horse-Battery1", username="alice")


class TestPasswordHashing:
    def test_hash_then_verify_round_trips(self) -> None:
        hashed = auth.hash_password("Correct-Horse1")
        assert auth._verify(hashed, "Correct-Horse1") is True
        assert auth._verify(hashed, "wrong-password") is False

    def test_hash_is_not_the_plaintext(self) -> None:
        hashed = auth.hash_password("Correct-Horse1")
        assert "Correct-Horse1" not in hashed


class TestAuthenticate:
    def test_unknown_username_raises_invalid_credentials(self, db_session: Session) -> None:
        with pytest.raises(auth.InvalidCredentialsError):
            auth.authenticate(db_session, "nobody", "whatever")

    def test_wrong_password_raises_invalid_credentials(self, db_session: Session) -> None:
        _make_user(db_session, "alice", "Correct-Horse1")
        with pytest.raises(auth.InvalidCredentialsError):
            auth.authenticate(db_session, "alice", "wrong-password")

    def test_correct_password_returns_the_user(self, db_session: Session) -> None:
        created = _make_user(db_session, "alice", "Correct-Horse1")
        user = auth.authenticate(db_session, "alice", "Correct-Horse1")
        assert user.id == created.id

    def test_username_is_case_and_whitespace_insensitive_on_lookup(
        self, db_session: Session
    ) -> None:
        _make_user(db_session, "alice", "Correct-Horse1")
        user = auth.authenticate(db_session, "  ALICE  ", "Correct-Horse1")
        assert user.username == "alice"

    def test_deactivated_account_looks_like_invalid_credentials(self, db_session: Session) -> None:
        user = _make_user(db_session, "alice", "Correct-Horse1")
        user.is_active = False
        db_session.flush()
        with pytest.raises(auth.InvalidCredentialsError):
            auth.authenticate(db_session, "alice", "Correct-Horse1")

    def test_repeated_failures_lock_the_account(self, db_session: Session) -> None:
        _make_user(db_session, "alice", "Correct-Horse1")
        for _ in range(auth.LOGIN_LOCKOUT_THRESHOLD):
            with pytest.raises(auth.InvalidCredentialsError):
                auth.authenticate(db_session, "alice", "wrong-password")
        with pytest.raises(auth.AccountLockedError):
            auth.authenticate(db_session, "alice", "Correct-Horse1")

    def test_lock_expires_after_the_lockout_duration(self, db_session: Session) -> None:
        user = _make_user(db_session, "alice", "Correct-Horse1")
        user.failed_login_count = auth.LOGIN_LOCKOUT_THRESHOLD
        user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        db_session.flush()
        result = auth.authenticate(db_session, "alice", "Correct-Horse1")
        assert result.username == "alice"
        assert result.locked_until is None
        assert result.failed_login_count == 0

    def test_successful_login_resets_failure_counter(self, db_session: Session) -> None:
        user = _make_user(db_session, "alice", "Correct-Horse1")
        user.failed_login_count = 2
        db_session.flush()
        auth.authenticate(db_session, "alice", "Correct-Horse1")
        assert user.failed_login_count == 0


class TestSessions:
    def test_create_session_returns_a_usable_token(self, db_session: Session) -> None:
        user = _make_user(db_session)
        issued = auth.create_session(db_session, user)
        assert issued.session.user_id == user.id
        # The plaintext token is never persisted verbatim.
        assert issued.token != issued.session.token_hash

    def test_validate_session_returns_the_user(self, db_session: Session) -> None:
        user = _make_user(db_session)
        issued = auth.create_session(db_session, user)
        db_session.flush()
        session = auth.validate_session(db_session, issued.token)
        assert session is not None
        assert session.user_id == user.id

    def test_validate_session_rejects_garbage_token(self, db_session: Session) -> None:
        assert auth.validate_session(db_session, "not-a-real-token") is None

    def test_validate_session_rejects_none(self, db_session: Session) -> None:
        assert auth.validate_session(db_session, None) is None

    def test_expired_session_is_rejected(self, db_session: Session) -> None:
        user = _make_user(db_session)
        issued = auth.create_session(db_session, user)
        issued.session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.flush()
        assert auth.validate_session(db_session, issued.token) is None

    def test_idle_session_is_rejected(self, db_session: Session) -> None:
        user = _make_user(db_session)
        issued = auth.create_session(db_session, user)
        issued.session.last_seen_at = (
            datetime.now(UTC) - auth.SESSION_IDLE_TIMEOUT - timedelta(seconds=1)
        )
        db_session.flush()
        assert auth.validate_session(db_session, issued.token) is None

    def test_revoked_session_is_rejected(self, db_session: Session) -> None:
        user = _make_user(db_session)
        issued = auth.create_session(db_session, user)
        db_session.flush()
        auth.revoke_session(db_session, issued.session)
        db_session.flush()
        assert auth.validate_session(db_session, issued.token) is None

    def test_session_for_deactivated_user_is_rejected(self, db_session: Session) -> None:
        user = _make_user(db_session)
        issued = auth.create_session(db_session, user)
        user.is_active = False
        db_session.flush()
        assert auth.validate_session(db_session, issued.token) is None

    def test_new_login_never_reuses_an_existing_session_token(self, db_session: Session) -> None:
        user = _make_user(db_session)
        first = auth.create_session(db_session, user)
        second = auth.create_session(db_session, user)
        assert first.token != second.token
        # Both remain independently valid — a fresh login does not by
        # itself revoke prior sessions.
        db_session.flush()
        assert auth.validate_session(db_session, first.token) is not None
        assert auth.validate_session(db_session, second.token) is not None

    def test_revoke_all_sessions_for_user(self, db_session: Session) -> None:
        user = _make_user(db_session)
        first = auth.create_session(db_session, user)
        second = auth.create_session(db_session, user)
        db_session.flush()
        auth.revoke_all_sessions_for_user(db_session, user)
        db_session.flush()
        assert auth.validate_session(db_session, first.token) is None
        assert auth.validate_session(db_session, second.token) is None
