#!/usr/bin/env python3
"""
MediX多智能体医疗助手 - 主入口
交互式对话；可选 -v / --verbose 开启详细日志
"""
import asyncio
import sys
import time
from pathlib import Path
from loguru import logger

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from swarm import process_with_swarm


def setup_logger(verbose: bool = False):
    """配置日志"""
    logger.remove()
    if verbose:
        logger.add(
            sys.stderr,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            level="DEBUG"
        )
    else:
        logger.add(
            sys.stderr,
            format="<level>{level: <8}</level> | <level>{message}</level>",
            level="INFO"
        )


async def interactive_mode():
    """交互式对话模式"""
    print("\n" + "🏥 " * 20)
    print(" " * 15 + "MediX多智能体医疗助手")
    print(" " * 15 + "智能群体协作系统")
    print("🏥 " * 20 + "\n")

    print("💡 使用说明：")
    print("  - 直接输入您的健康问题")
    print("  - 系统会自动判断使用单Agent还是多Agent协作")
    print("  - 输入 'exit' 或 'quit' 退出")
    print("  - 输入 'clear' 清屏")
    print("  - 输入 'help' 查看帮助")
    print("\n" + "-" * 60 + "\n")

    conversation_count = 0

    # 为整个交互式会话生成一个session_id
    import uuid
    from datetime import datetime
    session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
    logger.info(f"Interactive session started with session_id: {session_id}")

    while True:
        try:
            # 获取用户输入
            user_input = input("💬 您的问题：").strip()

            if not user_input:
                continue

            # 处理命令
            if user_input.lower() in ['exit', 'quit', 'q']:
                print("\n👋 感谢使用，祝您健康！\n")
                break

            if user_input.lower() == 'clear':
                print("\033[2J\033[H")  # 清屏
                continue

            if user_input.lower() == 'help':
                print("\n📖 帮助信息：")
                print("  exit/quit - 退出程序")
                print("  clear     - 清屏")
                print("  help      - 显示帮助")
                print("  其他输入  - 作为健康问题进行咨询\n")
                continue

            # 处理健康咨询
            conversation_count += 1
            print(f"\n🤖 智能协作系统启动中... (第 {conversation_count} 次咨询)\n")

            # 记录开始时间
            start_time = time.time()
            result = await process_with_swarm(user_input, session_id=session_id)
            end_time = time.time()

            # 计算执行时间
            execution_time = end_time - start_time

            # 显示系统决策和执行时间
            if result.get('swarm_enabled'):
                agents_count = len(result.get('agents_involved', []))
                timeout_occurred = result.get('timeout_occurred', False)

                if timeout_occurred and agents_count == 0:
                    print(f"⚠️  群体智能模式：系统超时，所有Agent未完成")
                elif timeout_occurred:
                    print(f"⚠️  群体智能模式：{agents_count} 个Agent完成（部分超时）")
                else:
                    print(f"🐝 群体智能模式：{agents_count} 个Agent协作")
            else:
                print(f"🤖 单Agent模式")

            # 打印执行时间
            print(f"⏱️  执行时间：{execution_time:.2f} 秒")

            # 显示回答
            print("\n📋 回答：")
            print("-" * 60)
            print(result['answer'])
            print("-" * 60)

            # 显示建议（如果有）
            if result.get('suggestions'):
                print(f"\n💡 核心建议 ({len(result['suggestions'])}条)：")
                for i, suggestion in enumerate(result['suggestions'], 1):
                    print(f"  {i}. {suggestion}")

            # 显示免责声明
            print(f"\n{result['disclaimer']}")
            print("\n" + "=" * 60 + "\n")

        except KeyboardInterrupt:
            print("\n\n👋 检测到中断信号，退出程序...\n")
            break
        except Exception as e:
            logger.error(f"处理请求时出错: {e}")
            print(f"\n❌ 抱歉，处理您的问题时出现错误：{e}\n")


def main():
    """主函数：启动交互式对话"""
    verbose = '-v' in sys.argv or '--verbose' in sys.argv
    setup_logger(verbose)

    try:
        asyncio.run(interactive_mode())
    except Exception as e:
        logger.error(f"程序运行出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
