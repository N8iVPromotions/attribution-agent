from __future__ import annotations

from utils import auth_store


def test_password_hash_round_trip():
    password_hash = auth_store.hash_password("correct horse")

    assert password_hash.startswith("pbkdf2_sha256$")
    assert auth_store.verify_password("correct horse", password_hash)
    assert not auth_store.verify_password("wrong", password_hash)


def test_login_bootstraps_admin_and_creates_session(monkeypatch):
    store = _FakeAuthStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setenv("ARIE_BOOTSTRAP_ADMIN_EMAIL", "zajen@n8ivpromotions.com")
    monkeypatch.setenv("ARIE_BOOTSTRAP_ADMIN_PASSWORD", "pilot-password")

    result = auth_store.login("ZAJEN@n8ivpromotions.com", "pilot-password")

    assert result.ok
    assert result.user is not None
    assert result.user.email == "zajen@n8ivpromotions.com"
    assert result.user.role.value == "admin"
    assert result.session_id in store.sessions
    assert store.events[-1]["outcome"] == "success"


def test_login_bad_password_increments_failed_count(monkeypatch):
    store = _FakeAuthStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setenv("ARIE_BOOTSTRAP_ADMIN_EMAIL", "zajen@n8ivpromotions.com")
    monkeypatch.setenv("ARIE_BOOTSTRAP_ADMIN_PASSWORD", "pilot-password")

    result = auth_store.login("zajen@n8ivpromotions.com", "wrong")

    assert not result.ok
    user = store.fetch_user("zajen@n8ivpromotions.com")
    assert user["failed_login_count"] == 1


def test_validate_session_returns_active_user(monkeypatch):
    store = _FakeAuthStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setenv("ARIE_BOOTSTRAP_ADMIN_EMAIL", "zajen@n8ivpromotions.com")
    monkeypatch.setenv("ARIE_BOOTSTRAP_ADMIN_PASSWORD", "pilot-password")
    result = auth_store.login("zajen@n8ivpromotions.com", "pilot-password")

    user = auth_store.validate_session(result.session_id)

    assert user is not None
    assert user.email == "zajen@n8ivpromotions.com"


class _FakeAuthStore:
    def __init__(self):
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.events: list[dict] = []

    def count_users(self):
        return len(self.users)

    def fetch_user(self, email):
        return self.users.get(email.lower())

    def upsert_user(self, record):
        row = dict(record)
        self.users[row["email"].lower()] = row

    def write_session(self, record):
        row = dict(record)
        self.sessions[row["session_id"]] = row

    def fetch_session(self, session_id):
        return self.sessions.get(session_id)

    def revoke_session(self, session_id):
        if session_id in self.sessions:
            self.sessions[session_id]["revoked_at"] = "now"

    def write_event(self, record):
        self.events.append(dict(record))


def _patch_store(monkeypatch, store: _FakeAuthStore) -> None:
    monkeypatch.setattr(auth_store.db, "count_auth_users", store.count_users)
    monkeypatch.setattr(auth_store.db, "fetch_auth_user_by_email", store.fetch_user)
    monkeypatch.setattr(auth_store.db, "upsert_auth_user", store.upsert_user)
    monkeypatch.setattr(auth_store.db, "write_auth_session", store.write_session)
    monkeypatch.setattr(auth_store.db, "fetch_auth_session", store.fetch_session)
    monkeypatch.setattr(auth_store.db, "revoke_auth_session", store.revoke_session)
    monkeypatch.setattr(auth_store.db, "write_auth_event", store.write_event)
