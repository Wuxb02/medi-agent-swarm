"""
批量迁移旧版档案文件到 SQLite

遍历 memory/profile/ 下所有用户目录，将 PERSONAL.md / PENDING.md
迁入 sessions.db 的 profiles 表（原文件重命名为 .bak）。

迁移本身是幂等的：DB 已有行的用户自动跳过，可重复运行。
首次实例化 PersonalProfile 时也会按用户惰性迁移，本脚本仅用于
部署前批量预热，非必须执行。

用法：
  python mediZJ/memory/scripts/migrate_profiles_to_db.py
"""
import sys
from pathlib import Path

# 将项目根目录加入 sys.path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger


def main():
    from mediZJ.memory.personal_profile import _PROFILE_DIR, PersonalProfile

    if not _PROFILE_DIR.exists():
        logger.info(f"档案目录不存在，无需迁移: {_PROFILE_DIR}")
        return

    migrated = 0
    for user_dir in sorted(_PROFILE_DIR.iterdir()):
        if not user_dir.is_dir():
            continue
        user_id = user_dir.name
        has_files = any(
            f.suffix == ".md" for f in user_dir.iterdir() if f.is_file()
        )
        if not has_files:
            continue
        # 实例化即触发惰性迁移（幂等）
        PersonalProfile(user_id=user_id)
        migrated += 1
        logger.info(f"已处理用户: {user_id}")

    # 最旧的全局单文件（profile/PERSONAL.md）由 default 用户迁移逻辑处理
    if (_PROFILE_DIR / "PERSONAL.md").exists() or (
        _PROFILE_DIR / "PENDING.md"
    ).exists():
        PersonalProfile(user_id="default")
        logger.info("已处理全局旧版文件（归入 default 用户）")

    logger.info(f"迁移完成，共处理 {migrated} 个用户目录")


if __name__ == "__main__":
    main()
