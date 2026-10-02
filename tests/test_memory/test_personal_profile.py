"""PersonalProfile（MySQL 存储）按 user_id 隔离的测试"""

import asyncio
import pytest
from mediZJ.infrastructure.database import transaction

from mediZJ.memory.personal_profile import PersonalProfile
from mediZJ.memory.session_db import SessionDB

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


@pytest.fixture
async def db(mysql_infrastructure):
    """每个用例使用独立的临时数据库"""
    SessionDB.reset()
    instance = SessionDB()
    async with transaction() as conn:
        for user_id in ("alice", "bob", "default"):
            await conn.execute(
                "INSERT INTO users(user_id,username,username_normalized,created_at) "
                "VALUES (%s,%s,%s,'now')",
                (user_id, user_id, user_id),
            )
    yield instance
    SessionDB.reset()


@pytest.fixture(autouse=True)
def profile_dir(tmp_path, monkeypatch):
    """重定向旧版档案目录，避免迁移逻辑触碰仓库真实文件"""
    return tmp_path / "profile"


async def test_profiles_isolated_between_users(db):
    """两个 user_id 的档案互不可见"""
    alice = PersonalProfile(user_id="alice", db=db)
    bob = PersonalProfile(user_id="bob", db=db)
    await alice.save({"年龄": "30岁"})
    await bob.save({"年龄": "45岁", "过敏史": "青霉素"})
    assert await PersonalProfile(user_id="alice", db=db).load() == {"年龄": "30岁"}
    assert await PersonalProfile(user_id="bob", db=db).load() == {
        "年龄": "45岁",
        "过敏史": "青霉素",
    }


async def test_pending_isolated_between_users(db):
    """待确认暂存区同样按用户隔离"""
    alice = PersonalProfile(user_id="alice", db=db)
    await alice.add_pending([{"key": "吸烟史", "value": "10年", "confidence": "high"}])
    assert len(await PersonalProfile(user_id="alice", db=db).load_pending()) == 1
    assert await PersonalProfile(user_id="bob", db=db).load_pending() == []


async def test_save_does_not_clobber_pending(db):
    """save() 只写 content 列，不清空 pending 列"""
    profile = PersonalProfile(user_id="alice", db=db)
    await profile.add_pending(
        [{"key": "吸烟史", "value": "10年", "confidence": "high"}]
    )
    await profile.save({"年龄": "30岁"})
    assert await profile.load() == {"年龄": "30岁"}
    assert len(await profile.load_pending()) == 1


async def test_default_user_when_no_user_id(db):
    """缺省 user_id 落到 default（向后兼容）"""
    profile = PersonalProfile(db=db)
    assert profile.user_id == "default"
    await profile.save({"性别": "男"})
    assert await profile.load() == {"性别": "男"}


def test_invalid_user_id_rejected(db):
    """非法 user_id（路径穿越等）被拒绝"""
    with pytest.raises(ValueError):
        PersonalProfile(user_id="../etc", db=db)
    with pytest.raises(ValueError):
        PersonalProfile(user_id="a/b", db=db)


async def test_concurrent_update_no_lost_update(db):
    """独立档案实例的并发更新通过 MySQL 行锁保护。"""
    await asyncio.gather(
        *(
            PersonalProfile(user_id="alice", db=db).update(
                [{"key": f"key{i}", "value": str(i)}]
            )
            for i in range(10)
        )
    )
    confirmed = await PersonalProfile(user_id="alice", db=db).load()
    assert confirmed == {f"key{i}": str(i) for i in range(10)}


async def test_existing_files_remain_untouched(db, profile_dir):
    profile_dir.mkdir(parents=True)
    legacy = profile_dir / "PERSONAL.md"
    text = "# 患者档案\n\n## 个人信息\n- 年龄：28岁\n"
    legacy.write_text(text, encoding="utf-8")
    assert await PersonalProfile(db=db).load() == {}
    assert legacy.read_text(encoding="utf-8") == text


async def test_records_pending_confirmation_replacement_and_text(db):
    from mediZJ.memory.personal_profile import MedicalRecord, PendingItem

    profile = PersonalProfile("alice", db)
    assert await profile.to_text() == "暂无"
    records = await profile.add_records(
        [
            {"date": "2024-01", "description": "旧记录"},
            {
                "date": "2025-01",
                "description": "新记录",
                "symptoms": "咳嗽",
                "medication": "处方药",
            },
            {"date": "", "description": "忽略"},
        ]
    )
    assert [record.date for record in records] == ["2025-01", "2024-01"]
    assert "咳嗽" in await profile.to_text()
    assert "用药：处方药" in records[0].to_line()
    await profile.save_records(
        [MedicalRecord("2025-03", "替换", duration="一周", outcome="已康复")]
    )
    assert len(await profile.load_records()) == 1
    await profile.add_pending_records(
        [
            {
                "date": "2025-04",
                "description": "待确认",
                "symptoms": "发热",
                "duration": "两天",
                "medication": "未用药",
            },
            {"date": "", "description": "忽略"},
        ]
    )
    pending = await profile.get_pending()
    assert pending[0].is_record and "待确认" in pending[0].to_line()
    assert await profile.confirm_pending("病史", "待确认")
    assert len(await profile.load_records()) == 2
    await profile.add_pending_records([{"date": "2025-05", "description": "待驳回"}])
    assert await profile.dismiss_pending("病史", "待驳回")
    assert not await profile.dismiss_pending("病史", "不存在")
    await profile.save_pending([PendingItem("年龄", "30岁", "2025-01-01", "medium")])
    assert "置信度：中" in (await profile.load_pending())[0].to_line()
    await profile.save_pending([PendingItem("性别", "女", "2025-01-01", "high")])
    assert [item.key for item in await profile.load_pending()] == ["性别"]
    await profile.save({"年龄": "30", "": "忽略"})
    assert "个人信息" in await profile.to_text()
    assert not await profile.confirm_pending("不存在", "不存在")


async def test_unknown_profile_owner_is_rejected(db):
    profile = PersonalProfile("missing-owner", db)
    with pytest.raises(LookupError):
        await profile.update([{"key": "年龄", "value": "30"}])
