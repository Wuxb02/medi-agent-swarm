"""显式清理已下线的 Mem0 历史数据。"""

import argparse
import asyncio
import os


async def cleanup(user_id: str) -> None:
    """删除指定用户的远程 Mem0 记忆。"""
    try:
        from mem0 import MemoryClient
    except ImportError as exc:
        raise RuntimeError("请先在运维环境单独安装 mem0ai") from exc

    api_key = os.getenv("MEM0_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("运维环境未配置 MEM0_API_KEY")
    client = MemoryClient(api_key=api_key)
    await asyncio.to_thread(
        client.delete_all,
        user_id=f"mediZJ_user_{user_id}",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("user_id")
    args = parser.parse_args()
    asyncio.run(cleanup(args.user_id))


if __name__ == "__main__":
    main()
