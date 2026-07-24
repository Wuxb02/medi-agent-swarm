"""
PersonalProfile：患者档案管理（两文件架构）

第一层：已确认信息 + 病史记录 → memory/profile/{user_id}/PERSONAL.md
第二层：待确认暂存区 → memory/profile/{user_id}/PENDING.md
"""
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from loguru import logger


# 文件路径（基于模块位置，不依赖工作目录）
_MODULE_DIR = Path(__file__).parent
_PROFILE_DIR = _MODULE_DIR / "profile"
# 旧版全局单文件路径（仅用于迁移到 default 用户目录）
PROFILE_PATH = _PROFILE_DIR / "PERSONAL.md"   # 已确认信息 + 病史记录
PENDING_PATH = _PROFILE_DIR / "PENDING.md"    # 待确认暂存区

_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 同一 user_id 跨实例共享的读写锁（多实例操作同一文件时串行化）
_user_locks: Dict[str, threading.RLock] = {}
_user_locks_guard = threading.Lock()


def _get_user_lock(user_id: str) -> threading.RLock:
    with _user_locks_guard:
        lock = _user_locks.get(user_id)
        if lock is None:
            lock = threading.RLock()
            _user_locks[user_id] = lock
        return lock


@dataclass
class MedicalRecord:
    """病史记录条目"""
    date: str           # "2024-03" 或 "2025-01-15"
    description: str    # "感冒"
    symptoms: str = ""  # "发烧、流涕"
    duration: str = ""  # "约一周"
    medication: str = ""  # "布洛芬"
    outcome: str = ""   # "已康复"

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
    key: str            # 信息类型："过敏史"；病史类型："病史"
    value: str          # 信息类型："青霉素过敏"；病史类型：病名（如"感冒"）
    source_date: str    # "2025-05-16"
    confidence: str     # "high" / "medium"（信息类型）；病史类型固定 "confirmed"
    # 病史专用字段（信息类型时为空）
    record_date: str = ""      # "2025-05"
    symptoms: str = ""         # "发烧、流涕"
    duration: str = ""         # "一周"
    medication: str = ""       # "布洛芬"
    outcome: str = ""          # ""

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
    """患者档案管理器（PERSONAL.md + PENDING.md，按 user_id 隔离）"""

    def __init__(self, user_id: str = "default"):
        if not _USER_ID_PATTERN.match(user_id):
            raise ValueError(f"非法 user_id: {user_id!r}（仅允许字母/数字/_/-，最长 64 字符）")
        self.user_id = user_id
        user_dir = _PROFILE_DIR / user_id
        self._profile_path = user_dir / "PERSONAL.md"
        self._pending_path = user_dir / "PENDING.md"
        self._lock = _get_user_lock(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_files()

    def _migrate_legacy_files(self):
        """将旧版全局单文件迁移到 default 用户目录（仅 default 用户执行一次）"""
        if self.user_id != "default":
            return
        with self._lock:
            # 从运行时 _PROFILE_DIR 推导旧路径（而非冻结的模块常量），
            # 保证测试重定向目录时迁移逻辑作用于同一棵目录树
            for legacy, target in (
                (_PROFILE_DIR / "PERSONAL.md", self._profile_path),
                (_PROFILE_DIR / "PENDING.md", self._pending_path),
            ):
                if legacy.exists() and not target.exists():
                    legacy.rename(target)
                    logger.info(f"已迁移旧版档案文件: {legacy} → {target}")

    # ========== PERSONAL.md 解析（包含确认信息 + 病史） ==========

    def _parse_profile(self) -> Dict[str, any]:
        """解析 PERSONAL.md，返回 {confirmed: Dict, records: List[MedicalRecord]}。

        新格式（有 ## 段落头）：
        ## 个人信息
        - 年龄：28岁
        ## 病史记录
        - [2024-03] 感冒：...

        旧格式（无 ## 头，纯 key-value）：
        - 年龄：28岁
        - 症状：发烧
        """
        if not self._profile_path.exists():
            return {"confirmed": {}, "records": []}

        with self._lock:
            content = self._profile_path.read_text(encoding="utf-8")

        # 检测是否有新格式的段落头
        has_sections = bool(re.search(r"^## ", content, re.MULTILINE))

        if not has_sections:
            # 旧格式：全部解析为 key-value
            return self._parse_old_format(content)

        # 新格式：按段落解析
        confirmed = {}
        records = []
        current_section = None

        record_pattern = re.compile(
            r"^- \[(\d{4}-\d{2}(?:-\d{2})?)\]\s*(.+)$"
        )

        for line in content.splitlines():
            line = line.strip()

            # 段落头
            section_match = re.match(r"^## (.+)$", line)
            if section_match:
                current_section = section_match.group(1).strip()
                continue

            if current_section == "病史记录":
                m = record_pattern.match(line)
                if m:
                    records.append(self._parse_record_body(m.group(1), m.group(2)))
            elif current_section and line.startswith("- ") and "：" in line:
                # key-value 行
                key_value = line[2:]
                key, value = key_value.split("：", 1)
                key = key.strip()
                value = value.strip()
                if key:
                    confirmed[key] = value

        return {"confirmed": confirmed, "records": records}

    def _parse_old_format(self, content: str) -> Dict[str, any]:
        """解析旧格式 PERSONAL.md（纯 key-value）。"""
        confirmed = {}
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("- ") and "：" in line:
                key_value = line[2:]
                key, value = key_value.split("：", 1)
                key = key.strip()
                value = value.strip()
                if key and key != "暂无":
                    confirmed[key] = value
        return {"confirmed": confirmed, "records": []}

    def _save_profile(self, confirmed: Dict[str, str], records: List[MedicalRecord]):
        """序列化并保存 PERSONAL.md。"""
        try:
            lines = ["# 患者档案", ""]

            # 个人信息段落
            lines.append("## 个人信息")
            if confirmed:
                for key, value in confirmed.items():
                    lines.append(f"- {key}：{value}")
            else:
                lines.append("- 暂无")
            lines.append("")

            # 病史记录段落
            lines.append("## 病史记录")
            if records:
                for record in records:
                    lines.append(record.to_line())
            else:
                lines.append("- 暂无")
            lines.append("")

            with self._lock:
                self._profile_path.write_text("\n".join(lines), encoding="utf-8")
            logger.debug(
                f"Saved profile: {len(confirmed)} confirmed + {len(records)} records"
            )
        except Exception as e:
            logger.error(f"Failed to save profile: {e}")

    # ========== 已确认信息 ==========

    def load(self) -> Dict[str, str]:
        """加载已确认的个人信息。"""
        return self._parse_profile()["confirmed"]

    def save(self, info: Dict[str, str]):
        """全量替换已确认的个人信息（保留病史记录）。"""
        with self._lock:
            data = self._parse_profile()
            self._save_profile(info, data["records"])

    def update(self, new_items: List[Dict[str, str]]) -> Dict[str, str]:
        """增量更新已确认信息（合并新旧数据）。"""
        with self._lock:
            data = self._parse_profile()
            confirmed = data["confirmed"]
            for item in new_items:
                key = item.get("key", "").strip()
                value = item.get("value", "").strip()
                if key and value:
                    confirmed[key] = value
            self._save_profile(confirmed, data["records"])
            return confirmed

    # ========== 病史记录 ==========

    def load_records(self) -> List[MedicalRecord]:
        """加载病史记录。"""
        return self._parse_profile()["records"]

    def _parse_record_body(self, date: str, body: str) -> MedicalRecord:
        """解析病史记录正文。

        格式：病名：症状，持续时间，用药：xxx，转归
        示例：感冒：发烧、流涕，持续约一周，用药：布洛芬，已康复
        """
        parts = [p.strip() for p in body.split("，") if p.strip()]
        if not parts:
            return MedicalRecord(date=date, description=body)

        first = parts[0]
        if "：" in first:
            desc, symptoms = first.split("：", 1)
            description = desc.strip()
            symptoms = symptoms.strip()
        else:
            description = first
            symptoms = ""

        duration = ""
        medication = ""
        outcome = ""

        for part in parts[1:]:
            if part.startswith("持续"):
                duration = part[2:]
            elif part.startswith("用药：") or part.startswith("用药:"):
                medication = part[3:].strip()
            elif part in ("已康复", "好转中", "未愈", "恶化", "痊愈"):
                outcome = part
            elif not symptoms:
                symptoms = part

        return MedicalRecord(
            date=date,
            description=description,
            symptoms=symptoms,
            duration=duration,
            medication=medication,
            outcome=outcome,
        )

    def save_records(self, records: List[MedicalRecord]):
        """保存病史记录（保留已确认信息）。"""
        with self._lock:
            data = self._parse_profile()
            self._save_profile(data["confirmed"], records)

    def add_records(self, new_records: List[Dict]) -> List[MedicalRecord]:
        """追加病史记录，按 (date, description) 去重。"""
        with self._lock:
            data = self._parse_profile()
            records = data["records"]
            existing_keys = {(r.date, r.description) for r in records}

            for item in new_records:
                record = MedicalRecord(
                    date=item.get("date", ""),
                    description=item.get("description", ""),
                    symptoms=item.get("symptoms", ""),
                    duration=item.get("duration", ""),
                    medication=item.get("medication", ""),
                    outcome=item.get("outcome", ""),
                )
                if not record.date or not record.description:
                    continue
                key = (record.date, record.description)
                if key not in existing_keys:
                    records.append(record)
                    existing_keys.add(key)
                    logger.info(f"  [MedicalRecord] [{record.date}] {record.description}")

            records.sort(key=lambda r: r.date, reverse=True)
            self._save_profile(data["confirmed"], records)
            return records

    # ========== 待确认暂存区（PENDING.md） ==========

    def load_pending(self) -> List[PendingItem]:
        """加载待确认条目（支持 [信息] 和 [病史] 两种类型）。"""
        if not self._pending_path.exists():
            return []
        try:
            with self._lock:
                content = self._pending_path.read_text(encoding="utf-8")
            items = []

            # 病史格式：- [病史][2025-05] 感冒，发烧，持续一周，用药：布洛芬（2025-05-16 提取）
            record_pattern = re.compile(
                r"^- \[病史\]\[(\d{4}-\d{2}(?:-\d{2})?)\]\s*(.+?)（(\d{4}-\d{2}-\d{2}) 提取）$"
            )
            # 信息格式：- [信息]过敏史：青霉素（2025-05-16 提取，置信度：高）
            info_pattern = re.compile(
                r"^- \[信息\](.+?)：(.+?)（(\d{4}-\d{2}-\d{2}) 提取，置信度：(高|中)）$"
            )
            # 兼容旧格式（无 [信息]/[病史] 前缀）：- 过敏史：青霉素（2025-05-16 提取，置信度：高）
            old_pattern = re.compile(
                r"^- (.+?)：(.+?)（(\d{4}-\d{2}-\d{2}) 提取，置信度：(高|中)）$"
            )

            for line in content.splitlines():
                line = line.strip()

                m = record_pattern.match(line)
                if m:
                    body = m.group(2)
                    parsed = self._parse_record_body(m.group(1), body)
                    items.append(PendingItem(
                        key="病史",
                        value=parsed.description,
                        source_date=m.group(3),
                        confidence="confirmed",
                        record_date=m.group(1),
                        symptoms=parsed.symptoms,
                        duration=parsed.duration,
                        medication=parsed.medication,
                        outcome=parsed.outcome,
                    ))
                    continue

                m = info_pattern.match(line)
                if m:
                    conf = "high" if m.group(4) == "高" else "medium"
                    items.append(PendingItem(
                        key=m.group(1).strip(),
                        value=m.group(2).strip(),
                        source_date=m.group(3),
                        confidence=conf,
                    ))
                    continue

                m = old_pattern.match(line)
                if m:
                    conf = "high" if m.group(4) == "高" else "medium"
                    items.append(PendingItem(
                        key=m.group(1).strip(),
                        value=m.group(2).strip(),
                        source_date=m.group(3),
                        confidence=conf,
                    ))

            return items
        except Exception as e:
            logger.error(f"Failed to load pending items: {e}")
            return []

    def save_pending(self, items: List[PendingItem]):
        """保存待确认条目。"""
        try:
            lines = ["# 待确认信息", ""]
            for item in items:
                lines.append(item.to_line())
            lines.append("")
            with self._lock:
                self._pending_path.write_text("\n".join(lines), encoding="utf-8")
            logger.debug(f"Saved {len(items)} pending items")
        except Exception as e:
            logger.error(f"Failed to save pending items: {e}")

    def add_pending(self, new_items: List[Dict]):
        """追加待确认条目（信息类型），按 (key, value) 去重。"""
        with self._lock:
            existing = self.load_pending()
            existing_keys = {(item.key, item.value) for item in existing}
            today = datetime.now().strftime("%Y-%m-%d")

            for item in new_items:
                key = item.get("key", "").strip()
                value = item.get("value", "").strip()
                confidence = item.get("confidence", "medium")
                if not key or not value:
                    continue
                if (key, value) in existing_keys:
                    continue
                existing.append(PendingItem(
                    key=key,
                    value=value,
                    source_date=today,
                    confidence=confidence,
                ))
                existing_keys.add((key, value))
                logger.info(f"  [Pending] {key}：{value}（置信度：{confidence}）")

            self.save_pending(existing)

    def add_pending_records(self, new_records: List[Dict]):
        """追加待确认病史条目，按 (record_date, value) 去重。"""
        with self._lock:
            existing = self.load_pending()
            existing_keys = {(item.key, item.value) for item in existing}
            today = datetime.now().strftime("%Y-%m-%d")

            for item in new_records:
                desc = item.get("description", "").strip()
                rec_date = item.get("date", "").strip()
                if not desc or not rec_date:
                    continue
                if ("病史", desc) in existing_keys:
                    continue
                existing.append(PendingItem(
                    key="病史",
                    value=desc,
                    source_date=today,
                    confidence="confirmed",
                    record_date=rec_date,
                    symptoms=item.get("symptoms", ""),
                    duration=item.get("duration", ""),
                    medication=item.get("medication", ""),
                    outcome=item.get("outcome", ""),
                ))
                existing_keys.add(("病史", desc))
                logger.info(f"  [Pending-Record] [{rec_date}] {desc}")

            self.save_pending(existing)

    def confirm_pending(self, key: str, value: str) -> bool:
        """确认待确认条目：从暂存区移入已确认信息或病史。"""
        with self._lock:
            items = self.load_pending()
            matched = [i for i in items if i.key == key and i.value == value]
            if not matched:
                return False

            item = matched[0]
            remaining = [i for i in items if not (i.key == key and i.value == value)]
            self.save_pending(remaining)

            if item.is_record:
                # 病史类型 → 写入 PERSONAL.md 病史记录
                self.add_records([{
                    "date": item.record_date,
                    "description": item.value,
                    "symptoms": item.symptoms,
                    "duration": item.duration,
                    "medication": item.medication,
                    "outcome": item.outcome,
                }])
                logger.info(f"Confirmed pending → records: [{item.record_date}] {item.value}")
            else:
                # 信息类型 → 写入已确认信息
                confirmed = self.load()
                confirmed[key] = value
                data = self._parse_profile()
                self._save_profile(confirmed, data["records"])
                logger.info(f"Confirmed pending → profile: {key}={value}")

            return True

    def dismiss_pending(self, key: str, value: str) -> bool:
        """丢弃待确认条目（不转入已确认）。"""
        with self._lock:
            items = self.load_pending()
            original_len = len(items)
            items = [i for i in items if not (i.key == key and i.value == value)]
            if len(items) < original_len:
                self.save_pending(items)
                logger.info(f"Dismissed pending: {key}={value}")
                return True
            return False

    def get_pending(self) -> List[PendingItem]:
        """获取所有待确认条目。"""
        return self.load_pending()

    # ========== Agent 上下文注入 ==========

    def to_text(self) -> str:
        """将已确认信息格式化为文本（注入 agent prompt）。

        仅输出已确认信息 + 已确认病史，不含待确认条目。
        """
        sections = []

        # 已确认信息
        confirmed = self.load()
        if confirmed:
            lines = [f"{k}：{v}" for k, v in confirmed.items()]
            sections.append("个人信息：\n" + "\n".join(lines))

        # 已确认病史记录
        records = self.load_records()
        if records:
            lines = [f"- {r.to_summary()}" for r in records[:10]]
            sections.append("病史记录：\n" + "\n".join(lines))

        if not sections:
            return "暂无"

        return "\n\n".join(sections)
