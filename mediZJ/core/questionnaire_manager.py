"""
QuestionnaireManager — 基于 asyncio.Future 的问卷暂停/恢复管理器

工作流程：
1. Agent 调用 question_for_user 工具 → 创建 Future 并 await
2. 前端收到 AGENT_QUESTIONNAIRE 事件 → 渲染问卷
3. 用户提交答案 → POST /api/chat/answer → resolve Future
4. Agent 恢复执行，用户答案作为 tool_result
"""
import asyncio
from typing import Dict, Any, Optional
from loguru import logger


class QuestionnaireManager:
    """问卷管理器：管理待回答的问卷 Future

    每个 session 拥有一个 QuestionnaireManager 实例，
    通过 questionnaire_id 路由答案到正确的 AgentLoop。
    """

    def __init__(self):
        self._pending: Dict[str, asyncio.Future] = {}

    async def create_pending(
        self,
        questionnaire_id: str,
        timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """创建 Future 并等待用户回答。

        由 AgentLoop 在检测到 needs_user_input 时调用。
        阻塞直到用户提交答案或超时。

        Args:
            questionnaire_id: 问卷唯一标识
            timeout: 超时秒数；None 表示无限等待（用户不回答则一直挂起）

        Returns:
            用户答案字典，如 {"q0": "35", "q1": "男", ...}

        Raises:
            TimeoutError: 用户未在指定时间内回答（仅 timeout 非 None 时）
        """
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[questionnaire_id] = future

        logger.info(f"问卷 {questionnaire_id} 等待用户回答（超时: {timeout if timeout is not None else '∞'}s）")

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.info(f"问卷 {questionnaire_id} 已收到用户回答")
            return result
        except asyncio.TimeoutError:
            logger.warning(f"问卷 {questionnaire_id} 超时（{timeout}s），用户未回答")
            raise
        finally:
            self._pending.pop(questionnaire_id, None)

    def resolve(self, questionnaire_id: str, answers: Dict[str, Any]) -> bool:
        """解析 Future，将用户答案注入等待中的 AgentLoop。

        由 API 端点（POST /api/chat/answer）调用。

        Args:
            questionnaire_id: 问卷唯一标识
            answers: 用户答案字典

        Returns:
            是否成功解析（False 表示未找到或已完成）
        """
        future = self._pending.get(questionnaire_id)
        if future is None:
            logger.warning(f"问卷 {questionnaire_id} 不存在或已完成")
            return False

        if future.done():
            logger.warning(f"问卷 {questionnaire_id} 已被解析")
            return False

        future.set_result(answers)
        logger.info(f"问卷 {questionnaire_id} 已解析，答案: {answers}")
        return True

    def cancel(self, questionnaire_id: str) -> bool:
        """取消待回答的问卷。

        会话清理时调用，避免 Future 泄漏。

        Args:
            questionnaire_id: 问卷唯一标识

        Returns:
            是否成功取消
        """
        future = self._pending.pop(questionnaire_id, None)
        if future and not future.done():
            future.cancel()
            logger.info(f"问卷 {questionnaire_id} 已取消")
            return True
        return False

    def cancel_all(self):
        """取消所有待回答的问卷（会话结束时清理）"""
        for qid in list(self._pending.keys()):
            self.cancel(qid)

    @property
    def has_pending(self) -> bool:
        """是否有待回答的问卷"""
        return bool(self._pending)

    @property
    def pending_ids(self) -> list:
        """所有待回答问卷的 ID 列表"""
        return list(self._pending.keys())
