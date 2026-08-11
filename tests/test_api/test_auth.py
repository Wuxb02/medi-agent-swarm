"""免密登录与用户数据隔离测试。"""

import hashlib
from datetime import datetime, timedelta

import pytest

from mediZJ.api.auth import AuthService
from mediZJ.memory.session_db import SessionDB


@pytest.fixture
def db(tmp_path):
    """每个用例使用独立数据库。"""

    SessionDB.reset()
    instance = SessionDB(str(tmp_path / "auth.db"))
    yield instance
    SessionDB.reset()


def test_login_auto_creates_and_reuses_user(db):
    """同一用户名忽略大小写并复用用户。"""

    service = AuthService(db)
    first, token, _ = service.login("Alice")
    second, _, _ = service.login("alice")

    assert first["user_id"] == second["user_id"]
    assert service.authenticate(token)["username"] == "Alice"


def test_admin_username_comes_from_environment(db, monkeypatch):
    """配置的管理员用户名获得管理员角色。"""

    monkeypatch.setenv("MEDIZJ_ADMIN_USERNAME", "root_user")
    user, _, _ = AuthService(db).login("ROOT_USER")

    assert user["role"] == "admin"


def test_legacy_profile_is_attached_to_new_account(db):
    """旧版以用户名为键的画像迁移到新用户 UUID。"""

    db.upsert_profile("legacy_user", content="旧画像")
    user, _, _ = AuthService(db).login("legacy_user")

    assert db.get_profile(user["user_id"])["content"] == "旧画像"
    assert db.get_profile("legacy_user") is None


def test_logout_and_expired_session_are_rejected(db):
    """退出及过期令牌不能继续认证。"""

    service = AuthService(db)
    user, token, _ = service.login("bob")
    service.logout(token)
    assert service.authenticate(token) is None

    expired_token = "expired-token"
    db.save_auth_session(
        hashlib.sha256(expired_token.encode("utf-8")).hexdigest(),
        user["user_id"],
        (datetime.now() - timedelta(seconds=1)).isoformat(),
    )
    assert service.authenticate(expired_token) is None


def test_session_rows_are_isolated_by_user(db):
    """会话列表、详情和删除均按 user_id 隔离。"""

    alice = db.get_or_create_user("alice")
    bob = db.get_or_create_user("bob")
    db.save_turn(
        session_id="session-a",
        turn_index=0,
        user_msg={"content": "问题", "timestamp": datetime.now().isoformat()},
        assistant_msg={"content": "回答", "timestamp": datetime.now().isoformat()},
        user_id=alice["user_id"],
    )

    assert db.get_session("session-a", alice["user_id"]) is not None
    assert db.get_session("session-a", bob["user_id"]) is None
    assert db.count_sessions(alice["user_id"]) == 1
    assert db.count_sessions(bob["user_id"]) == 0
    assert db.delete_session("session-a", bob["user_id"]) is False
    assert db.delete_session("session-a", alice["user_id"]) is True


def test_cannot_append_to_another_users_session(db):
    """伪造相同 session_id 不能追加消息。"""

    alice = db.get_or_create_user("alice")
    bob = db.get_or_create_user("bob")
    message = {"content": "内容", "timestamp": datetime.now().isoformat()}
    db.save_turn("shared", 0, message, message, user_id=alice["user_id"])

    with pytest.raises(PermissionError):
        db.save_turn("shared", 1, message, message, user_id=bob["user_id"])


def test_api_requires_login_and_sets_cookie(db, monkeypatch):
    """业务 API 需要登录，登录后可恢复当前用户。"""

    from fastapi.testclient import TestClient

    import mediZJ.api.auth as auth_module
    from mediZJ.api.main import app

    monkeypatch.setattr(auth_module, "_auth_service", AuthService(db))
    with TestClient(app) as client:
        assert client.get("/api/sessions").status_code == 401

        login_response = client.post(
            "/api/auth/login",
            json={"username": "api_user"},
        )
        assert login_response.status_code == 200
        assert "medizj_session" in login_response.cookies

        me_response = client.get("/api/auth/me")
        assert me_response.status_code == 200
        assert me_response.json()["username"] == "api_user"
        assert client.get("/api/sessions").status_code == 200


def test_regular_user_cannot_mutate_knowledge(db, monkeypatch):
    """普通用户无法执行知识库写操作。"""

    from fastapi.testclient import TestClient

    import mediZJ.api.auth as auth_module
    from mediZJ.api.main import app

    monkeypatch.setattr(auth_module, "_auth_service", AuthService(db))
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "normal_user"})
        response = client.delete("/api/knowledge/documents/not-found")
        assert response.status_code == 403


def test_evolution_operations_require_admin(db, monkeypatch):
    """自进化观测和治理接口仅管理员可访问。"""

    from fastapi.testclient import TestClient

    import mediZJ.api.auth as auth_module
    from mediZJ.api.main import app
    from mediZJ.evolution.service import EvolutionService
    from mediZJ.evolution.storage import EvolutionStorage

    EvolutionStorage.reset()
    EvolutionService.reset()
    storage = EvolutionStorage(db.db_path)
    EvolutionService(storage=storage)
    monkeypatch.setattr(auth_module, "_auth_service", AuthService(db))

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "normal_user"})
        assert client.get("/api/evolution/overview").status_code == 403
        assert client.get("/api/evolution/jobs").status_code == 403

        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"username": "admin"})
        assert client.get("/api/evolution/overview").status_code == 200
        assert client.get("/api/evolution/jobs").status_code == 200

    EvolutionService.reset()
    EvolutionStorage.reset()
