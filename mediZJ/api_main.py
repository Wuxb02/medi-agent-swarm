"""Web 服务启动入口

用法：
    python api_main.py              # 默认 0.0.0.0:8000
    python api_main.py --port 8080  # 指定端口
"""
import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="MediZJ Agent Swarm Web Server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = parser.parse_args()

    uvicorn.run(
        "mediZJ.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
