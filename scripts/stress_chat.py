"""MediZJ 问答接口并发压测脚本

用法：
    # 先启动后端：uv run python mediZJ/api_main.py

    # 干跑模式（打 history 端点，不消耗 LLM token，用于验证脚本与服务连通性）
    python scripts/stress_chat.py --dry-run --tiers 10,30,50

    # 正式压测（打 /api/chat，真实调用 LLM，会消耗 token！）
    python scripts/stress_chat.py --tiers 10,30,50

    # 自定义
    python scripts/stress_chat.py --tiers 20 --question "头痛怎么办" --timeout 180

观测重点：
    - 各档位成功率与状态码分布（LLM_MAX_CONCURRENCY 钳制下应无 429/5xx）
    - p50/p95 延迟随并发的增长曲线（排队应表现为延迟上升而非失败）
    - 压测期间检查服务端日志无 database is locked / Traceback
"""
import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Dict, List

import httpx


@dataclass
class TierResult:
    """单档位压测结果"""
    concurrency: int
    latencies: List[float] = field(default_factory=list)
    status_codes: Dict[int, int] = field(default_factory=dict)
    errors: Dict[str, int] = field(default_factory=dict)
    wall_time: float = 0.0

    @property
    def total(self) -> int:
        return sum(self.status_codes.values()) + sum(self.errors.values())

    @property
    def success_count(self) -> int:
        return self.status_codes.get(200, 0)


def _percentile(sorted_values: List[float], pct: float) -> float:
    """计算百分位数（最近秩法），输入需已排序"""
    if not sorted_values:
        return 0.0
    rank = max(1, round(len(sorted_values) * pct / 100))
    return sorted_values[min(rank, len(sorted_values)) - 1]


async def _one_request(
    client: httpx.AsyncClient,
    url: str,
    payload: dict | None,
    timeout: float,
    result: TierResult,
):
    """执行单次请求并记录延迟/状态码/异常"""
    start = time.perf_counter()
    try:
        if payload is None:
            resp = await client.get(url, timeout=timeout)
        else:
            resp = await client.post(url, json=payload, timeout=timeout)
        elapsed = time.perf_counter() - start
        result.latencies.append(elapsed)
        code = resp.status_code
        result.status_codes[code] = result.status_codes.get(code, 0) + 1
    except Exception as e:
        # 超时/连接错误等客户端侧异常单独归类
        elapsed = time.perf_counter() - start
        result.latencies.append(elapsed)
        kind = type(e).__name__
        result.errors[kind] = result.errors.get(kind, 0) + 1


async def run_tier(
    base_url: str,
    concurrency: int,
    question: str,
    timeout: float,
    dry_run: bool,
) -> TierResult:
    """以指定并发数打满一轮请求"""
    result = TierResult(concurrency=concurrency)
    limits = httpx.Limits(
        max_connections=concurrency + 10,
        max_keepalive_connections=concurrency + 10,
    )
    # trust_env=False：禁用系统/环境代理。macOS 系统代理会被 getproxies() 读取，
    # 而 httpx 不识别系统 bypass 列表，localhost 请求会被代理截获返回 502
    async with httpx.AsyncClient(
        base_url=base_url, limits=limits, trust_env=False
    ) as client:
        if dry_run:
            # 干跑：打 history 端点（走 SQLite 读路径，无 LLM 成本）
            tasks = [
                _one_request(
                    client, f"/api/chat/history/stress-{i}", None, timeout, result
                )
                for i in range(concurrency)
            ]
        else:
            # 正式：每个请求独立 session_id，避免 per-session 锁把压测串行化
            # （压测目标是系统整体吞吐，而非同会话排队）
            batch_id = int(time.time())
            tasks = [
                _one_request(
                    client,
                    "/api/chat",
                    {"question": question, "session_id": f"stress-{batch_id}-{i}"},
                    timeout,
                    result,
                )
                for i in range(concurrency)
            ]
        start = time.perf_counter()
        await asyncio.gather(*tasks)
        result.wall_time = time.perf_counter() - start
    return result


def print_tier_report(result: TierResult):
    """打印单档位报告"""
    lat = sorted(result.latencies)
    status_str = ", ".join(
        f"{code}:{n}" for code, n in sorted(result.status_codes.items())
    ) or "-"
    error_str = ", ".join(
        f"{kind}:{n}" for kind, n in sorted(result.errors.items())
    ) or "-"
    rps = result.total / result.wall_time if result.wall_time > 0 else 0.0

    print(f"\n=== 并发 {result.concurrency} ===")
    print(f"  总请求: {result.total}  成功(200): {result.success_count}  "
          f"成功率: {result.success_count / max(result.total, 1):.1%}")
    print(f"  状态码: {status_str}")
    print(f"  客户端异常: {error_str}")
    print(f"  延迟(s): p50={_percentile(lat, 50):.2f}  "
          f"p95={_percentile(lat, 95):.2f}  "
          f"p99={_percentile(lat, 99):.2f}  max={lat[-1] if lat else 0:.2f}")
    print(f"  墙钟时间: {result.wall_time:.1f}s  吞吐: {rps:.2f} req/s")


async def main():
    parser = argparse.ArgumentParser(description="MediZJ 问答接口并发压测")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--tiers", default="10,30,50",
                        help="逗号分隔的并发档位，默认 10,30,50")
    parser.add_argument("--question", default="最近有点失眠，应该注意什么？",
                        help="压测用问题（建议用简单问题控制 token 消耗）")
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="单请求超时（秒），需大于 REQUEST_TIMEOUT")
    parser.add_argument("--dry-run", action="store_true",
                        help="干跑模式：打 history 端点，不消耗 LLM token")
    args = parser.parse_args()

    tiers = [int(t) for t in args.tiers.split(",")]
    mode = "干跑（history 端点，无 LLM 成本）" if args.dry_run else "正式（/api/chat，消耗 LLM token）"
    print(f"目标: {args.base_url}  模式: {mode}  档位: {tiers}")
    if not args.dry_run:
        total = sum(tiers)
        print(f"⚠️  将发起 {total} 次真实 LLM 问答请求，"
              f"每次可能触发多轮 LLM 调用，请确认配额充足。")

    for tier in tiers:
        result = await run_tier(
            args.base_url, tier, args.question, args.timeout, args.dry_run
        )
        print_tier_report(result)

    print("\n提示：请同时检查服务端日志，确认无 database is locked / Traceback。")
    # 输出机器可读摘要，便于存档对比
    print(json.dumps({"tiers": tiers, "dry_run": args.dry_run}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
