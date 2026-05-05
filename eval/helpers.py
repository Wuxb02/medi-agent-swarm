"""
评估辅助工具

测试隔离：session_id 生成、PersonalProfile mock
"""
import uuid
from pathlib import Path
from contextlib import contextmanager

# PersonalProfile 全局文件路径（与 memory/personal_profile.py 一致）
_PERSONAL_PROFILE_PATH = Path("memory/PERSONAL.md")


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
    from swarm.swarm_coordinator import SwarmCoordinator

    # 备份原始 PersonalProfile 内容
    original_content = None
    if _PERSONAL_PROFILE_PATH.exists():
        original_content = _PERSONAL_PROFILE_PATH.read_text(encoding="utf-8")

    try:
        # 重置为空，避免评估间信息泄漏
        _PERSONAL_PROFILE_PATH.write_text("# 患者个人信息\n\n- 暂无：无\n", encoding="utf-8")

        coordinator = SwarmCoordinator()
        # 同步到所有 Worker 的 user_context
        for worker in coordinator.worker_pool:
            if hasattr(worker, 'loop'):
                worker.loop.user_context = coordinator.personal_profile.to_text()
        yield coordinator
    finally:
        # 恢复原始内容
        if original_content is not None:
            _PERSONAL_PROFILE_PATH.write_text(original_content, encoding="utf-8")
        else:
            _PERSONAL_PROFILE_PATH.unlink(missing_ok=True)
