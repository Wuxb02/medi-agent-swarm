"""患者档案与待确认信息，使用 MySQL 事务和所有者行锁。"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional


from .session_db import SessionDB
from contextlib import asynccontextmanager
from mediZJ.infrastructure.database import transaction


_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class MedicalRecord:
    """病史记录条目"""

    date: str  # "2024-03" 或 "2025-01-15"
    description: str  # "感冒"
    symptoms: str = ""  # "发烧、流涕"
    duration: str = ""  # "约一周"
    medication: str = ""  # "布洛芬"
    outcome: str = ""  # "已康复"

    def to_line(self) -> str:
        """序列化为 Markdown 行"""
        parts = [self.description]
        if self.symptoms:
            parts.append(self.symptoms)
        if self.duration:
            parts.append(f"持续{self.duration}")
        if self.medication:
            parts.append(f"用药：{self.medication}")
        if self.outcome:
            parts.append(self.outcome)
        return f"- [{self.date}] {'，'.join(parts)}"

    def to_summary(self) -> str:
        """简要摘要（用于 agent 上下文）"""
        parts = [self.description]
        if self.symptoms:
            parts.append(self.symptoms)
        if self.medication:
            parts.append(f"用药：{self.medication}")
        return f"[{self.date}] {'，'.join(parts)}"


@dataclass
class PendingItem:
    """待确认条目（支持信息类型和病史类型）

    信息类型：key/value 有值，record 字段为空
    病史类型：key="病史"，record 字段有值
    """

    key: str  # 信息类型："过敏史"；病史类型："病史"
    value: str  # 信息类型："青霉素过敏"；病史类型：病名（如"感冒"）
    source_date: str  # "2025-05-16"
    confidence: str  # "high" / "medium"（信息类型）；病史类型固定 "confirmed"
    # 病史专用字段（信息类型时为空）
    record_date: str = ""  # "2025-05"
    symptoms: str = ""  # "发烧、流涕"
    duration: str = ""  # "一周"
    medication: str = ""  # "布洛芬"
    outcome: str = ""  # ""

    @property
    def is_record(self) -> bool:
        return self.key == "病史"

    def to_line(self) -> str:
        """序列化为 Markdown 行"""
        if self.is_record:
            parts = [self.value]
            if self.symptoms:
                parts.append(self.symptoms)
            if self.duration:
                parts.append(f"持续{self.duration}")
            if self.medication:
                parts.append(f"用药：{self.medication}")
            if self.outcome:
                parts.append(self.outcome)
            return f"- [病史][{self.record_date}] {'，'.join(parts)}（{self.source_date} 提取）"
        else:
            conf_label = "高" if self.confidence == "high" else "中"
            return f"- [信息]{self.key}：{self.value}（{self.source_date} 提取，置信度：{conf_label}）"


class PersonalProfile:
    """患者档案管理器（profiles 表，按 user_id 隔离）"""

    def __init__(self, user_id: str = "default", db: Optional[SessionDB] = None):
        if not _USER_ID_PATTERN.match(user_id):
            raise ValueError(
                f"非法 user_id: {user_id!r}（仅允许字母/数字/_/-，最长 64 字符）"
            )
        self.user_id = user_id
        self._db = db if db is not None else SessionDB()
        from .structured_memory import StructuredMemoryStore

        self._structured = StructuredMemoryStore(self._db)

    @asynccontextmanager
    async def _transaction(self):
        async with transaction() as conn:
            row = (
                await conn.execute(
                    "SELECT user_id FROM users WHERE user_id=%s FOR UPDATE",
                    (self.user_id,),
                )
            ).fetchone()
            if row is None:
                raise LookupError("档案所有者不存在")
            yield

    async def load(self) -> Dict[str, str]:
        """加载已确认个人信息。"""
        items = await self._structured.list_items(
            self.user_id, statuses=("active",), memory_type="profile_fact"
        )
        return {item["memory_key"]: str(item["value"]) for item in items}

    async def save(self, info: Dict[str, str]) -> None:
        """全量替换已确认个人信息。"""
        async with self._transaction():
            normalized = {
                str(key).strip(): str(value).strip()
                for key, value in info.items()
                if str(key).strip() and str(value).strip()
            }
            (
                await self._structured.replace_active(
                    self.user_id, "profile_fact", normalized, actor_id=self.user_id
                )
            )

    async def update(self, new_items: List[Dict[str, str]]) -> Dict[str, str]:
        """增量更新已确认信息。"""
        async with self._transaction():
            for item in new_items:
                key = item.get("key", "").strip()
                value = item.get("value", "").strip()
                if key and value:
                    (
                        await self._structured.upsert_active(
                            self.user_id, "profile_fact", key, value
                        )
                    )
            return await self.load()

    async def load_records(self) -> List[MedicalRecord]:
        """加载已确认病史。"""
        records = []
        for item in await self._structured.list_items(
            self.user_id, statuses=("active",), memory_type="medical_record"
        ):
            value = item["value"]
            records.append(
                MedicalRecord(
                    date=str(value.get("date", "")),
                    description=str(value.get("description", "")),
                    symptoms=str(value.get("symptoms", "")),
                    duration=str(value.get("duration", "")),
                    medication=str(value.get("medication", "")),
                    outcome=str(value.get("outcome", "")),
                )
            )
        return sorted(records, key=lambda record: record.date, reverse=True)

    async def save_records(self, records: List[MedicalRecord]) -> None:
        """全量替换已确认病史。"""
        values = {
            self._record_key(record): {
                "date": record.date,
                "description": record.description,
                "symptoms": record.symptoms,
                "duration": record.duration,
                "medication": record.medication,
                "outcome": record.outcome,
            }
            for record in records
            if record.date and record.description
        }
        async with self._transaction():
            (
                await self._structured.replace_active(
                    self.user_id, "medical_record", values, actor_id=self.user_id
                )
            )

    async def add_records(self, new_records: List[Dict]) -> List[MedicalRecord]:
        """增量添加病史。"""
        async with self._transaction():
            for item in new_records:
                record = MedicalRecord(
                    date=str(item.get("date", "")).strip(),
                    description=str(item.get("description", "")).strip(),
                    symptoms=str(item.get("symptoms", "")),
                    duration=str(item.get("duration", "")),
                    medication=str(item.get("medication", "")),
                    outcome=str(item.get("outcome", "")),
                )
                if not record.date or not record.description:
                    continue
                (
                    await self._structured.upsert_active(
                        self.user_id,
                        "medical_record",
                        self._record_key(record),
                        {
                            "date": record.date,
                            "description": record.description,
                            "symptoms": record.symptoms,
                            "duration": record.duration,
                            "medication": record.medication,
                            "outcome": record.outcome,
                        },
                    )
                )
            return await self.load_records()

    async def load_pending(self) -> List[PendingItem]:
        """加载待确认记忆。"""
        result = []
        for item in await self._structured.list_items(
            self.user_id, statuses=("pending",)
        ):
            value = item["value"]
            if item["memory_type"] == "medical_record":
                result.append(
                    PendingItem(
                        key="病史",
                        value=str(value.get("description", "")),
                        source_date=item["created_at"][:10],
                        confidence=self._confidence_label(item["confidence"]),
                        record_date=str(value.get("date", "")),
                        symptoms=str(value.get("symptoms", "")),
                        duration=str(value.get("duration", "")),
                        medication=str(value.get("medication", "")),
                        outcome=str(value.get("outcome", "")),
                    )
                )
            else:
                result.append(
                    PendingItem(
                        key=item["memory_key"],
                        value=str(value),
                        source_date=item["created_at"][:10],
                        confidence=self._confidence_label(item["confidence"]),
                    )
                )
        return result

    async def save_pending(self, items: List[PendingItem]) -> None:
        """兼容评测辅助接口，全量替换待确认项。"""
        existing = await self._structured.list_items(
            self.user_id, statuses=("pending",)
        )
        for existing_item in existing:
            (
                await self._structured._set_status(
                    existing_item["memory_id"],
                    self.user_id,
                    "dismissed",
                    "replace_pending",
                )
            )
        for pending_item in items:
            (await self._add_pending_item(pending_item))

    async def add_pending(self, new_items: List[Dict]) -> None:
        """添加待确认个人信息。"""
        for item in new_items:
            key = str(item.get("key", "")).strip()
            value = str(item.get("value", "")).strip()
            if not key or not value:
                continue
            (
                await self._structured.add_pending(
                    self.user_id,
                    "profile_fact",
                    key,
                    value,
                    confidence=self._confidence_value(item.get("confidence", "medium")),
                )
            )

    async def add_pending_records(self, new_records: List[Dict]) -> None:
        """添加待确认病史。"""
        for item in new_records:
            record = MedicalRecord(
                date=str(item.get("date", "")).strip(),
                description=str(item.get("description", "")).strip(),
                symptoms=str(item.get("symptoms", "")),
                duration=str(item.get("duration", "")),
                medication=str(item.get("medication", "")),
                outcome=str(item.get("outcome", "")),
            )
            if record.date and record.description:
                (
                    await self._structured.add_pending(
                        self.user_id,
                        "medical_record",
                        self._record_key(record),
                        self._pending_value(
                            PendingItem(
                                key="病史",
                                value=record.description,
                                source_date=datetime.now(timezone.utc).strftime(
                                    "%Y-%m-%d"
                                ),
                                confidence="medium",
                                record_date=record.date,
                                symptoms=record.symptoms,
                                duration=record.duration,
                                medication=record.medication,
                                outcome=record.outcome,
                            )
                        ),
                    )
                )

    async def confirm_pending(self, key: str, value: str) -> bool:
        """确认待确认记忆。"""
        memory_key = key
        if key == "病史":
            match = next(
                (
                    item
                    for item in (await self.load_pending())
                    if item.is_record and item.value == value
                ),
                None,
            )
            if match:
                memory_key = self._record_key_from_pending(match)
        return await self._structured.confirm_pending(self.user_id, memory_key, value)

    async def dismiss_pending(self, key: str, value: str) -> bool:
        """驳回待确认记忆。"""
        memory_key = key
        if key == "病史":
            match = next(
                (
                    item
                    for item in (await self.load_pending())
                    if item.is_record and item.value == value
                ),
                None,
            )
            if match:
                memory_key = self._record_key_from_pending(match)
        return await self._structured.dismiss_pending(self.user_id, memory_key, value)

    async def get_pending(self) -> List[PendingItem]:
        return await self.load_pending()

    async def to_text(self) -> str:
        """以确定性字段顺序输出已确认用户画像。"""
        sections = []
        confirmed = await self.load()
        if confirmed:
            lines = [f"{key}：{confirmed[key]}" for key in sorted(confirmed)]
            sections.append("个人信息：\n" + "\n".join(lines))
        records = await self.load_records()
        if records:
            lines = [f"- {record.to_summary()}" for record in records]
            sections.append("病史记录：\n" + "\n".join(lines))
        return "\n\n".join(sections) if sections else "暂无"

    async def _add_pending_item(self, item: PendingItem) -> None:
        (
            await self._structured.add_pending(
                self.user_id,
                "medical_record" if item.is_record else "profile_fact",
                self._record_key_from_pending(item) if item.is_record else item.key,
                self._pending_value(item),
                confidence=self._confidence_value(item.confidence),
            )
        )

    @staticmethod
    def _record_key(record: MedicalRecord) -> str:
        return f"{record.date}:{record.description}"

    @staticmethod
    def _record_key_from_pending(item: PendingItem) -> str:
        return f"{item.record_date}:{item.value}"

    @staticmethod
    def _pending_value(item: PendingItem):
        if not item.is_record:
            return item.value
        return {
            "date": item.record_date,
            "description": item.value,
            "symptoms": item.symptoms,
            "duration": item.duration,
            "medication": item.medication,
            "outcome": item.outcome,
        }

    @staticmethod
    def _confidence_value(value) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        return {"high": 0.9, "confirmed": 0.9, "medium": 0.6}.get(str(value), 0.5)

    @staticmethod
    def _confidence_label(value: float) -> str:
        return "high" if float(value) >= 0.8 else "medium"
