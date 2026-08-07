"""
评估辅助工具

测试隔离：session_id 生成、PersonalProfile 隔离
"""
import uuid
from contextlib import contextmanager

from mediZJ.memory.personal_profile import PersonalProfile


def make_session_id(prefix: str) -> str:
    """生成隔离的评估用 session_id，避免 LTM 交叉污染"""
    return f"eval-{prefix}-{uuid.uuid4().hex[:8]}"


@contextmanager
def isolated_coordinator():
    """
    创建评估专用 SwarmCoordinator，自动处理 PersonalProfile 隔离

    使用方式：
        with isolated_coordinator() as coordinator:
            result = await coordinator.process(question, session_id=session_id)
    """
    from mediZJ.swarm.swarm_coordinator import SwarmCoordinator

    # 备份 default 用户的档案（与 coordinator 同源，走同一个 SessionDB 单例）
    profile = PersonalProfile("default")
    backup_info = profile.load()
    backup_records = profile.load_records()
    backup_pending = profile.load_pending()

    try:
        # 重置为空，避免评估间信息泄漏
        profile.save({})
        profile.save_records([])
        profile.save_pending([])

        coordinator = SwarmCoordinator()
        # 同步到所有 Worker 的 user_context
        coordinator._refresh_worker_profiles()
        yield coordinator
    finally:
        # 恢复原始内容
        profile.save(backup_info)
        profile.save_records(backup_records)
        profile.save_pending(backup_pending)
