"""PersonalProfile（SQLite 存储）按 user_id 隔离的测试"""
import threading

import pytest

from mediZJ.memory import personal_profile as pp_module
from mediZJ.memory.personal_profile import PersonalProfile
from mediZJ.memory.session_db import SessionDB


@pytest.fixture
def db(tmp_path):
    """每个用例使用独立的临时数据库"""
    SessionDB.reset()
    instance = SessionDB(str(tmp_path / "sessions.db"))
    yield instance
    SessionDB.reset()


@pytest.fixture(autouse=True)
def profile_dir(tmp_path, monkeypatch):
    """重定向旧版档案目录，避免迁移逻辑触碰仓库真实文件"""
    monkeypatch.setattr(pp_module, "_PROFILE_DIR", tmp_path / "profile")
    return pp_module._PROFILE_DIR


def test_profiles_isolated_between_users(db):
    """两个 user_id 的档案互不可见"""
    alice = PersonalProfile(user_id="alice", db=db)
    bob = PersonalProfile(user_id="bob", db=db)

    alice.save({"年龄": "30岁"})
    bob.save({"年龄": "45岁", "过敏史": "青霉素"})

    assert PersonalProfile(user_id="alice", db=db).load() == {"年龄": "30岁"}
    assert PersonalProfile(user_id="bob", db=db).load() == {
        "年龄": "45岁", "过敏史": "青霉素"
    }


def test_pending_isolated_between_users(db):
    """待确认暂存区同样按用户隔离"""
    alice = PersonalProfile(user_id="alice", db=db)
    bob = PersonalProfile(user_id="bob", db=db)

    alice.add_pending([{"key": "吸烟史", "value": "10年", "confidence": "high"}])

    assert len(PersonalProfile(user_id="alice", db=db).load_pending()) == 1
    assert PersonalProfile(user_id="bob", db=db).load_pending() == []


def test_save_does_not_clobber_pending(db):
    """save() 只写 content 列，不清空 pending 列"""
    profile = PersonalProfile(user_id="alice", db=db)
    profile.add_pending([{"key": "吸烟史", "value": "10年", "confidence": "high"}])

    profile.save({"年龄": "30岁"})

    assert profile.load() == {"年龄": "30岁"}
    assert len(profile.load_pending()) == 1


def test_default_user_when_no_user_id(db):
    """缺省 user_id 落到 default（向后兼容）"""
    profile = PersonalProfile(db=db)
    assert profile.user_id == "default"
    profile.save({"性别": "男"})
    assert db.get_profile("default") is not None


def test_invalid_user_id_rejected(db):
    """非法 user_id（路径穿越等）被拒绝"""
    with pytest.raises(ValueError):
        PersonalProfile(user_id="../etc", db=db)
    with pytest.raises(ValueError):
        PersonalProfile(user_id="a/b", db=db)


def test_legacy_files_migrated_to_default(db, profile_dir):
    """旧版全局单文件自动迁移入库（归入 default 用户）"""
    profile_dir.mkdir(parents=True)
    legacy_text = "# 患者档案\n\n## 个人信息\n- 年龄：28岁\n"
    legacy = profile_dir / "PERSONAL.md"
    legacy.write_text(legacy_text, encoding="utf-8")

    profile = PersonalProfile(user_id="default", db=db)

    # 全局文件消失，入库内容与原文件逐字节一致，中间文件改名 .bak
    assert not legacy.exists()
    assert db.get_profile("default")["content"] == legacy_text
    assert (profile_dir / "default" / "PERSONAL.md.bak").exists()
    assert profile.load() == {"年龄": "28岁"}


def test_user_files_migrated_idempotently(db, profile_dir):
    """用户目录文件迁移入库，且重复实例化不产生重复迁移"""
    user_dir = profile_dir / "alice"
    user_dir.mkdir(parents=True)
    personal_text = "# 患者档案\n\n## 个人信息\n- 年龄：30岁\n"
    pending_text = "# 待确认信息\n\n- [信息]吸烟史：10年（2025-05-16 提取，置信度：高）\n"
    (user_dir / "PERSONAL.md").write_text(personal_text, encoding="utf-8")
    (user_dir / "PENDING.md").write_text(pending_text, encoding="utf-8")

    PersonalProfile(user_id="alice", db=db)

    row = db.get_profile("alice")
    assert row["content"] == personal_text
    assert row["pending"] == pending_text
    assert not (user_dir / "PERSONAL.md").exists()
    assert (user_dir / "PERSONAL.md.bak").exists()
    assert (user_dir / "PENDING.md.bak").exists()

    # 二次实例化：DB 已有行，跳过迁移；修改 DB 内容后不会被文件覆盖
    db.upsert_profile("alice", content="# 患者档案\n\n## 个人信息\n- 年龄：31岁\n")
    profile = PersonalProfile(user_id="alice", db=db)
    assert profile.load() == {"年龄": "31岁"}


def test_concurrent_update_no_lost_update(db):
    """多线程并发 update 不丢失更新（共享 user 锁）"""
    profile = PersonalProfile(user_id="alice", db=db)

    def worker(n: int):
        PersonalProfile(user_id="alice", db=db).update(
            [{"key": f"key{n}", "value": str(n)}]
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    confirmed = profile.load()
    for i in range(10):
        assert confirmed.get(f"key{i}") == str(i)
