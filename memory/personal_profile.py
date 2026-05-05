"""
PersonalProfile：患者个人信息本地持久化（全局单文件）

所有会话共享一个 memory/PERSONAL.md 文件，记录：
- 年龄、性别、身高、体重等基本信息
- 既往病史、家族病史
- 过敏史、当前用药
- 生活习惯
"""
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger


# 全局个人信息文件路径
PROFILE_PATH = Path("memory/PERSONAL.md")


class PersonalProfile:
    """患者个人信息管理器（全局单文件）"""

    def __init__(self):
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, str]:
        """加载个人信息。"""
        if not PROFILE_PATH.exists():
            return {}

        try:
            content = PROFILE_PATH.read_text(encoding="utf-8")
            info = {}
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("- ") and "：" in line:
                    key_value = line[2:]
                    key, value = key_value.split("：", 1)
                    info[key.strip()] = value.strip()
            return info
        except Exception as e:
            logger.error(f"Failed to load personal profile: {e}")
            return {}

    def save(self, info: Dict[str, str]):
        """保存个人信息。"""
        try:
            lines = ["# 患者个人信息", ""]
            for key, value in info.items():
                lines.append(f"- {key}：{value}")
            lines.append("")
            PROFILE_PATH.write_text("\n".join(lines), encoding="utf-8")
            logger.debug(f"Saved personal profile: {len(info)} items")
        except Exception as e:
            logger.error(f"Failed to save personal profile: {e}")

    def update(self, new_items: List[Dict[str, str]]) -> Dict[str, str]:
        """增量更新个人信息（合并新旧数据）。"""
        existing = self.load()
        for item in new_items:
            key = item.get("key", "").strip()
            value = item.get("value", "").strip()
            if key and value:
                existing[key] = value

        self.save(existing)
        return existing

    def to_text(self) -> str:
        """将个人信息格式化为文本（用于注入 prompt）。"""
        info = self.load()
        if not info:
            return "暂无"
        return "\n".join(f"{k}：{v}" for k, v in info.items())
