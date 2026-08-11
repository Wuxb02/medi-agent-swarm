"""自进化编排服务。"""

import asyncio
import hashlib
import json
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .judge import ConversationJudge
from .config import EvolutionSettings
from .storage import EvolutionStorage


class EvolutionService:
    """负责反馈入队、异步评审与运行时经验检索。"""

    _instance: Optional["EvolutionService"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        storage: Optional[EvolutionStorage] = None,
        judge: Optional[ConversationJudge] = None,
    ) -> None:
        if hasattr(self, "_initialized"):
            return
        self.storage = storage or EvolutionStorage()
        self.settings = EvolutionSettings.from_env()
        self._judge = judge
        self._worker_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self.enabled = self.settings.enabled
        self._initialized = True

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @property
    def judge(self) -> ConversationJudge:
        if self._judge is None:
            self._judge = ConversationJudge()
        return self._judge

    def submit_feedback(
        self,
        message_id: int,
        user_id: str,
        rating: str,
        reason_codes: List[str],
        comment: str,
    ) -> Dict[str, Any]:
        feedback = self.storage.upsert_feedback(
            message_id,
            user_id,
            rating,
            reason_codes,
            comment,
        )
        job_id = self.storage.enqueue_job(
            message_id,
            user_id,
            "user_feedback",
            feedback["version"],
            feedback,
        )
        feedback["evaluation_job_id"] = job_id
        return feedback

    def maybe_enqueue_sample(self, message_id: int, user_id: str) -> None:
        """按确定性采样率将无反馈回答加入评审队列。"""
        if not self.enabled:
            return
        rate = self.settings.sample_rate
        digest = hashlib.sha256(str(message_id).encode("utf-8")).digest()
        sample = int.from_bytes(digest[:8], "big") / (2**64 - 1)
        if sample < rate:
            self.storage.enqueue_job(message_id, user_id, "sampling")

    def enqueue_manual(self, message_id: int) -> Optional[str]:
        context = self.storage.get_message_context(message_id)
        if context is None:
            raise LookupError("回答不存在")
        return self.storage.enqueue_job(
            message_id,
            context["user_id"],
            "manual",
            time.time_ns(),
        )

    def get_runtime_experiences(
        self,
        user_id: str,
        question: str,
        limit: int = 4,
    ) -> List[Dict[str, Any]]:
        """以词项覆盖度排序已发布经验。"""
        candidates = self.storage.get_active_experiences(user_id)
        query_terms = self._terms(question)
        ranked = []
        for item in candidates:
            pattern_terms = self._terms(item.get("query_pattern", ""))
            overlap = len(query_terms & pattern_terms)
            if pattern_terms and overlap == 0:
                continue
            ranked.append((overlap, float(item.get("average_score", 0)), item))
        ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)
        return [item for _, _, item in ranked[:limit]]

    def get_runtime_context(self, user_id: str, question: str) -> Dict[str, Any]:
        experiences = self.get_runtime_experiences(user_id, question)
        if not experiences:
            return {}
        lines = []
        assignments = []
        applied = []
        for item in experiences:
            bucket = "active"
            should_apply = True
            if item.get("status") == "observing":
                should_apply = self._in_observation_sample(
                    user_id,
                    question,
                    item["experience_id"],
                )
                bucket = "treatment" if should_apply else "control"
            assignments.append(
                {
                    "experience_id": item["experience_id"],
                    "bucket": bucket,
                    "applied": should_apply,
                }
            )
            if not should_apply:
                continue
            line = f"- [{item['experience_type']}] {item['content']}"
            prerequisites = self._json_list(item.get("prerequisites"))
            exclusions = self._json_list(item.get("exclusions"))
            if prerequisites:
                line += f"\n  使用前确认：{'、'.join(prerequisites)}"
            if exclusions:
                line += f"\n  不适用于：{'、'.join(exclusions)}"
            if item.get("safety_notes"):
                line += f"\n  安全警示：{item['safety_notes']}"
            lines.append(line)
            applied.append(item["experience_id"])
        return {
            "verified_experiences": "\n".join(lines),
            "applied_experience_ids": applied,
            "experience_assignments": assignments,
        }

    @staticmethod
    def _terms(text: str) -> set[str]:
        normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
        words = set(normalized.split())
        compact = normalized.replace(" ", "")
        words.update(compact[index:index + 2] for index in range(len(compact) - 1))
        return {word for word in words if word}

    @staticmethod
    def _json_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if not isinstance(value, str):
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    def _in_observation_sample(
        self,
        user_id: str,
        question: str,
        experience_id: str,
    ) -> bool:
        rate = self.settings.observation_rate
        key = f"{user_id}:{question}:{experience_id}".encode("utf-8")
        sample = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
        return sample / (2**64 - 1) < rate

    async def start(self) -> None:
        if not self.enabled or self._worker_task is not None:
            return
        self._stop_event = asyncio.Event()
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        await self._worker_task
        self._worker_task = None

    async def process_one(self) -> bool:
        job = await asyncio.to_thread(self.storage.claim_job)
        if job is None:
            return False
        try:
            context = await asyncio.to_thread(
                self.storage.get_message_context,
                int(job["assistant_message_id"]),
                job["user_id"],
            )
            if context is None:
                raise LookupError("评审对象不存在或无权访问")
            snapshot = job.get("feedback_snapshot")
            if snapshot:
                context["feedback"] = json.loads(snapshot)
            result = await asyncio.wait_for(
                self.judge.evaluate(context),
                timeout=self.settings.judge_timeout,
            )
            await asyncio.to_thread(
                self.storage.save_evaluation,
                job,
                result,
                self.judge.model_name,
            )
        except Exception as exc:
            logger.exception("自进化评审任务失败: {}", job["job_id"])
            await asyncio.to_thread(
                self.storage.fail_job,
                job["job_id"],
                str(exc),
            )
        return True

    async def _worker_loop(self) -> None:
        assert self._stop_event is not None
        interval = self.settings.poll_interval
        while not self._stop_event.is_set():
            processed = await self.process_one()
            if processed:
                continue
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
