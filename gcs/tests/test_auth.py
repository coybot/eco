import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import AuthStore, user_id_from_authorization_header  # noqa: E402


def test_generates_and_persists_operator_token(tmp_path):
    path = tmp_path / "auth.json"
    store = AuthStore(path, operator_names=["alice"])
    token = store.operator_tokens()["alice"]
    assert token

    reopened = AuthStore(path, operator_names=["alice"])
    assert reopened.operator_tokens()["alice"] == token  # stable across restarts


def test_operator_user_id_lookup(tmp_path):
    store = AuthStore(tmp_path / "auth.json", operator_names=["alice"])
    token = store.operator_tokens()["alice"]
    assert store.operator_user_id_for_token(token) == "gcs-alice"
    assert store.operator_user_id_for_token("wrong-token") is None
    assert store.operator_user_id_for_token("") is None


def test_authorization_header_parsing(tmp_path):
    store = AuthStore(tmp_path / "auth.json", operator_names=["alice"])
    token = store.operator_tokens()["alice"]
    assert user_id_from_authorization_header(store, f"Bearer {token}") == "gcs-alice"
    assert user_id_from_authorization_header(store, "Bearer wrong") is None
    assert user_id_from_authorization_header(store, None) is None
    assert user_id_from_authorization_header(store, "Basic abc") is None


def test_drone_tokens_are_per_drone_and_stable(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    t1 = store.ensure_drone_token("d1")
    t2 = store.ensure_drone_token("d2")
    assert t1 != t2
    assert store.ensure_drone_token("d1") == t1  # idempotent
    assert store.drone_id_for_token(t1) == "d1"
    assert store.drone_id_for_token("nope") is None


def test_permissions_are_owner_only(tmp_path):
    path = tmp_path / "auth.json"
    AuthStore(path, operator_names=["alice"])
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600
