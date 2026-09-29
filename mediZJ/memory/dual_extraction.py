"""最终回答后的个人信息与医学知识双轨提取。"""

import asyncio
import json
from typing import Any

from loguru import logger

from mediZJ.core.jev_client import JevClient
from mediZJ.knowledge.candidate_service import KnowledgeCandidateService
from mediZJ.knowledge.catalog import _now


_PERSONAL_GATE = {
    "type": "choice",
    "instructions": "用户原话是否明确陈述用户本人的个人健康事实或患病经历？",
    "criteria": {
        "yes": "第一人称或明确自述的年龄、疾病、症状、过敏、用药等。",
        "no": "一般医学咨询、假设、他人经历、只有助手回答。",
    },
}
_KNOWLEDGE_GATE = {
    "type": "choice",
    "instructions": "本轮是否含有可独立核验的通用医学知识主张？",
    "criteria": {
        "yes": "疾病、症状、诊断、治疗或预防的一般性主张。",
        "no": "仅个人病情、寒暄或无可核验医学断言。",
    },
}


class DualMemoryExtractor:
    """并行运行两个相互独立的提取任务。"""

    def __init__(
        self,
        llm_client: Any,
        profile: Any,
        jev: JevClient | None = None,
        candidates: KnowledgeCandidateService | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.profile = profile
        self.jev = jev or JevClient()
        self.candidates = candidates or KnowledgeCandidateService()

    async def process(
        self, turn_id: str, user_id: str, question: str, answer: str
    ) -> None:
        results = await asyncio.gather(
            self._run_lane(turn_id, "personal",
                           self._personal(question)),
            self._run_lane(turn_id, "knowledge",
                           self._knowledge(turn_id, user_id, question, answer)),
            return_exceptions=True,
        )
        for lane, result in zip(("personal", "knowledge"), results):
            if isinstance(result, BaseException):
                logger.warning("记忆提取轨道异常: turn={} lane={} error={}",
                               turn_id, lane, result)

    async def _run_lane(
        self, turn_id: str, lane: str, task: Any
    ) -> None:
        catalog = self.candidates.catalog
        with catalog._connection() as conn:
            row = conn.execute(
                """SELECT status FROM memory_extraction_runs
                WHERE turn_id = ? AND lane = ?""",
                (turn_id, lane),
            ).fetchone()
            if row and row["status"] in {"running", "done"}:
                task.close()
                return
            conn.execute(
                """INSERT OR REPLACE INTO memory_extraction_runs
                (turn_id, lane, status, error, updated_at)
                VALUES (?, ?, 'running', NULL, ?)""",
                (turn_id, lane, _now()),
            )
        try:
            await task
            status, error = "done", None
        except asyncio.CancelledError:
            status, error = "failed", "cancelled"
            raise
        except Exception as exc:
            status, error = "failed", type(exc).__name__
            logger.warning("记忆提取失败: turn={} lane={} error={}",
                           turn_id, lane, exc)
        finally:
            with catalog._connection() as conn:
                conn.execute(
                    """UPDATE memory_extraction_runs SET status = ?, error = ?,
                    updated_at = ? WHERE turn_id = ? AND lane = ?""",
                    (status, error, _now(), turn_id, lane),
                )

    async def _gate(self, question: str, name: str, spec: dict) -> bool:
        choice, _ = await self.jev.choice(
            question, name, spec, {"yes", "no"}
        )
        return choice == "yes"

    async def _personal(self, question: str) -> None:
        if not await self._gate(question, "personal", _PERSONAL_GATE):
            return
        prompt = (
            "仅从下列用户原话提取用户本人事实。不得使用助手回答、"
            "第三人称、假设或一般咨询。输出 JSON，含 stable_info 数组"
            "(key,value,confidence,source_text) 和 medical_records 数组"
            "(date,description,symptoms,duration,medication,outcome,source_text)。"
            "每个 source_text 必须是用户原话中的逐字片段。"
            "无法确定本人归属时输出空数组。\n用户原话：" + question
        )
        raw = await self.llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = json.loads(raw)
        facts = result.get("stable_info", [])
        records = result.get("medical_records", [])
        if not isinstance(facts, list) or not isinstance(records, list):
            raise ValueError("个人信息提取格式无效")
        existing = self.profile.load()
        pending = self.profile.load_pending()
        pending_facts = {(item.key, item.value) for item in pending}
        unique_facts = [
            item for item in facts
            if isinstance(item, dict)
            and item.get("key") and item.get("value")
            and item.get("source_text")
            and item["source_text"] in question
            and existing.get(item["key"]) != item["value"]
            and (item["key"], item["value"]) not in pending_facts
        ]
        current_records = self.profile.load_records()
        record_keys = {(item.date, item.description) for item in current_records}
        record_keys.update(
            (item.record_date, item.value)
            for item in pending if item.is_record
        )
        unique_records = [
            item for item in records
            if isinstance(item, dict) and item.get("date")
            and item.get("description")
            and item.get("source_text")
            and item["source_text"] in question
            and (item["date"], item["description"]) not in record_keys
        ]
        self.profile.add_pending(unique_facts)
        self.profile.add_pending_records(unique_records)

    async def _knowledge(
        self, turn_id: str, user_id: str, question: str, answer: str
    ) -> None:
        if not await self._gate(question + "\n" + answer, "knowledge",
                                _KNOWLEDGE_GATE):
            return
        prompt = (
            "从对话提取可独立核验的通用医学知识主张。"
            "不得包含患者个人资料、诊断推测或个人化建议。"
            "仅输出 JSON：{\"claims\":[{\"claim\":\"...\","
            "\"source_text\":\"对话中逐字原文\"}]}。每条原文必须逐字出现于对话。"
            "\n用户：" + question + "\n助手：" + answer
        )
        raw = await self.llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        claims = json.loads(raw).get("claims", [])
        if not isinstance(claims, list):
            raise ValueError("医学知识提取格式无效")
        for item in claims[:10]:
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim", "")).strip()
            source_text = str(item.get("source_text", "")).strip()
            if not claim or not source_text or source_text not in question + answer:
                continue
            hits = await asyncio.to_thread(self.candidates.evidence_hits, claim)
            if any(claim in hit["excerpt"] for hit in hits):
                continue
            evidence = await self._judge_evidence(claim, hits)
            self.candidates.add_candidate(
                turn_id, user_id, claim, source_text, evidence
            )

    async def _judge_evidence(
        self, claim: str, hits: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result = []
        for hit in hits:
            prompt = (
                "比较医学主张与资料片段。仅输出 JSON："
                "{\"verdict\":\"support|conflict|irrelevant\","
                "\"quote\":\"片段中的逐字短句\"}。"
                "不能仅凭主题相近判断支持。\n主张：" + claim
                + "\n片段：" + hit["excerpt"]
            )
            raw = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                response_format={"type": "json_object"},
            )
            judged = json.loads(raw)
            verdict = judged.get("verdict")
            quote = judged.get("quote", "")
            if verdict in {"support", "conflict"} and len(quote) >= 8 \
                    and quote in hit["excerpt"]:
                result.append({**hit, "verdict": verdict, "quote": quote})
        return result
