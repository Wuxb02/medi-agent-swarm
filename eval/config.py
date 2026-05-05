"""
评估框架配置

阈值、模型参数、Baseline LLM 配置
"""
import os

# ===== 评估阈值 =====
THRESHOLDS = {
    "routing_accuracy": 0.95,
    "retrieval_accuracy": 0.87,
    "single_agent_latency_max": 15.0,   # 秒
    "swarm_latency_max": 30.0,           # 秒
    "multiturn_accuracy": 0.92,
    "abtest_system_score": 4.5,
    "abtest_baseline_score": 3.9,
}

# ===== 评估参数 =====
LATENCY_RUNS = 3              # 响应时间每题重复次数
ROUTING_RUNS = 3               # 路由投票次数（多数投票）
RETRIEVAL_TOP_K = 5            # 检索返回 top-k

# ===== 数据集路径 =====
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")

ROUTING_CASES_PATH = os.path.join(DATA_DIR, "routing_cases.json")
RETRIEVAL_CASES_PATH = os.path.join(DATA_DIR, "retrieval_cases.json")
MULTITURN_CASES_PATH = os.path.join(DATA_DIR, "multiturn_cases.json")
ABTEST_CASES_PATH = os.path.join(DATA_DIR, "abtest_cases.json")

# ===== AB 测试 Baseline 配置 =====
# 从 .env 读取，若未配置则使用主 LLM 配置（同模型无包装）
BASELINE_LLM_API_KEY = os.getenv("BASELINE_LLM_API_KEY", os.getenv("LLM_API_KEY"))
BASELINE_LLM_BASE_URL = os.getenv("BASELINE_LLM_BASE_URL", os.getenv("LLM_BASE_URL"))
BASELINE_LLM_MODEL_NAME = os.getenv("BASELINE_LLM_MODEL_NAME", os.getenv("LLM_MODEL_NAME", "gpt-4o"))
