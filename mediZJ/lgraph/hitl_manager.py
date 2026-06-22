"""
HITL (Human-in-the-Loop) 管理

基于 LangGraph interrupt() / Command(resume=...) 的问卷交互管理。
替代 asyncio.Future 阻塞模式，实现图的挂起和恢复。

问卷不设超时：用户不回答则图一直挂起等待。
"""
import asyncio
import uuid
from typing import Dict, Any, Optional, List, Callable
from loguru import logger


class HITLManager:
    """
    LangGraph HITL 管理器

    管理 interrupt 状态和 resume 数据传递。
    与 QuestionnaireManager 不同，这里不需要 asyncio.Future，
    因为 LangGraph 的 interrupt() 本身就是持久化的挂起机制。

    使用方式：
        # 在图的 interrupt_before 节点中
        user_input = interrupt({"type": "questionnaire", "data": {...}})

        # 外部通过 Command 恢复
        graph.ainvoke(Command(resume={"answers": {...}}), config)
    """

    def __init__(self):
        # 记录当前活跃的 interrupt 信息（用于前端状态查询）
        self._pending_interrupts: Dict[str, Dict[str, Any]] = {}
        # session_id -> 恢复锁
        self._resume_events: Dict[str, asyncio.Event] = {}
        # session_id -> 用户答案
        self._pending_answers: Dict[str, Dict[str, Any]] = {}

    def register_interrupt(self, session_id: str, interrupt_data: Dict[str, Any]):
        """注册一个 interrupt 事件（由 API 层调用）"""
        self._pending_interrupts[session_id] = {
            **interrupt_data,
            "registered_at": None,  # 由调用方设置时间戳
        }
        logger.info(f"[HITL] interrupt registered: session={session_id}, "
                    f"type={interrupt_data.get('type', 'unknown')}")

    def clear_interrupt(self, session_id: str):
        """清除 interrupt 状态（resume 后调用）"""
        self._pending_interrupts.pop(session_id, None)
        self._pending_answers.pop(session_id, None)
        logger.info(f"[HITL] interrupt cleared: session={session_id}")

    def get_pending(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取待处理的 interrupt 信息"""
        return self._pending_interrupts.get(session_id)

    def set_answer(self, session_id: str, answers: Dict[str, Any]):
        """设置用户回答（供 resume 使用）"""
        self._pending_answers[session_id] = answers
        logger.info(f"[HITL] answer set for session={session_id}")

    def get_answer(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取用户回答"""
        return self._pending_answers.get(session_id)

    def has_pending(self, session_id: str) -> bool:
        """检查是否有待处理的 interrupt"""
        return session_id in self._pending_interrupts

    @property
    def pending_ids(self) -> List[str]:
        """获取所有待处理 interrupt 的 session ID 列表"""
        return list(self._pending_interrupts.keys())

    def cancel_all(self, session_id: str = None):
        """取消所有（或指定 session 的）interrupt"""
        if session_id:
            self.clear_interrupt(session_id)
        else:
            self._pending_interrupts.clear()
            self._pending_answers.clear()


# 兼容 QuestionnaireManager 接口的适配器
class HITLAdapter:
    """
    将 HITLManager 适配为 QuestionnaireManager 接口

    用于在 SwarmCoordinator 中无缝替换 QuestionnaireManager，
    保持 LeadAgent.clarify() 等现有代码不变。
    """

    def __init__(self, hitl_manager: HITLManager, session_id: str):
        self._hitl = hitl_manager
        self._session_id = session_id

    async def create_pending(self, questionnaire_id: str, timeout: float = None) -> Dict[str, Any]:
        """
        创建待处理问卷（替代 QuestionnaireManager.create_pending）

        在 LangGraph 模式下，这个方法是"虚拟"的——
        问卷已经通过 interrupt() 发送到前端，这里只是等待 API 层的 resume。
        """
        # 非阻塞检查：如果答案已经通过 resume 注入，直接返回
        answer = self._hitl.get_answer(self._session_id)
        if answer:
            return answer

        # 否则返回空（实际的上层 interrupt 已经挂起了图执行）
        # 这里不会被执行到，因为图在到达此方法前已经被 interrupt() 挂起
        logger.warning("[HITLAdapter] create_pending 在非 interrupt 路径被调用，返回降级答案")
        return {}

    @property
    def has_pending(self) -> bool:
        """检查是否有待处理的问卷"""
        return self._hitl.has_pending(self._session_id)

    @property
    def pending_ids(self) -> List[str]:
        """获取待处理问卷 ID 列表"""
        if self._hitl.has_pending(self._session_id):
            pending = self._hitl.get_pending(self._session_id)
            if pending:
                qid = pending.get("questionnaire_id", "")
                return [qid] if qid else []
        return []

    def cancel(self, questionnaire_id: str):
        """取消待处理问卷"""
        self._hitl.clear_interrupt(self._session_id)

    def cancel_all(self):
        """取消所有问卷"""
        self._hitl.cancel_all(self._session_id)
