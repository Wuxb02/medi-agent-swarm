"""PersonalProfile 按 user_id 隔离的测试"""
import pytest

from mediZJ.memory import personal_profile as pp_module
from mediZJ.memory.personal_profile import PersonalProfile


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    """将档案根目录重定向到临时目录，避免污染仓库"""
    monkeypatch.setattr(pp_module, "_PROFILE_DIR", tmp_path / "profile")
    return pp_module._PROFILE_DIR


def test_profiles_isolated_between_users(profile_dir):
    """两个 user_id 的档案互不可见"""
    alice = PersonalProfile(user_id="alice")
    bob = PersonalProfile(user_id="bob")

    alice.save({"年龄": "30岁"})
    bob.save({"年龄": "45岁", "过敏史": "青霉素"})

    assert PersonalProfile(user_id="alice").load() == {"年龄": "30岁"}
    assert PersonalProfile(user_id="bob").load() == {"年龄": "45岁", "过敏史": "青霉素"}


def test_pending_isolated_between_users(profile_dir):
    """待确认暂存区同样按用户隔离"""
    alice = PersonalProfile(user_id="alice")
    bob = PersonalProfile(user_id="bob")

    alice.add_pending([{"key": "吸烟史", "value": "10年", "confidence": "high"}])

    assert len(PersonalProfile(user_id="alice").load_pending()) == 1
    assert PersonalProfile(user_id="bob").load_pending() == []


def test_default_user_when_no_user_id(profile_dir):
    """缺省 user_id 落到 default 目录（向后兼容）"""
    profile = PersonalProfile()
    assert profile.user_id == "default"
    profile.save({"性别": "男"})
    assert (profile_dir / "default" / "PERSONAL.md").exists()


def test_invalid_user_id_rejected(profile_dir):
    """非法 user_id（路径穿越等）被拒绝"""
    with pytest.raises(ValueError):
        PersonalProfile(user_id="../etc")
    with pytest.raises(ValueError):
        PersonalProfile(user_id="a/b")


def test_legacy_files_migrated_to_default(profile_dir):
    """旧版全局单文件自动迁移到 default 用户目录"""
    profile_dir.mkdir(parents=True)
    legacy = profile_dir / "PERSONAL.md"
    legacy.write_text("# 患者档案\n\n## 个人信息\n- 年龄：28岁\n", encoding="utf-8")

    profile = PersonalProfile(user_id="default")

    assert not legacy.exists()
    assert (profile_dir / "default" / "PERSONAL.md").exists()
    assert profile.load() == {"年龄": "28岁"}


def test_concurrent_update_no_lost_update(profile_dir):
    """多线程并发 update 不丢失更新（共享 user 锁）"""
    import threading

    profile = PersonalProfile(user_id="alice")

    def worker(n: int):
        PersonalProfile(user_id="alice").update(
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
