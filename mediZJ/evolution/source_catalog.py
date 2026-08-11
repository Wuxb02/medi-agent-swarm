"""失败归因的安全源码追溯目录。"""

from pathlib import Path
from typing import Any, Dict, List


_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_SOURCES: Dict[str, Dict[str, str]] = {
    "prompt.lead_system": {
        "label": "Lead Agent 系统提示词",
        "path": "mediZJ/prompt/swarm/lead_system.j2",
        "symbol": "Lead Agent",
    },
    "prompt.assessment": {
        "label": "任务分解提示词",
        "path": "mediZJ/prompt/swarm/assessment_user.j2",
        "symbol": "问题：",
    },
    "prompt.synthesis": {
        "label": "结果综合提示词",
        "path": "mediZJ/prompt/swarm/synthesis.j2",
        "symbol": "用户原始问题",
    },
    "retrieval.memory": {
        "label": "记忆与相似案例检索",
        "path": "mediZJ/lgraph/supervisor_graph.py",
        "symbol": "async def _retrieve_memories",
    },
    "retrieval.knowledge": {
        "label": "医学知识库检索",
        "path": "mediZJ/knowledge/milvus_kb.py",
        "symbol": "def search(",
    },
    "tool.registry": {
        "label": "工具注册与调度",
        "path": "mediZJ/lgraph/tool_registry.py",
        "symbol": "class ToolRegistry",
    },
    "tool.execution": {
        "label": "Agent 工具执行",
        "path": "mediZJ/lgraph/tool_executor.py",
        "symbol": "async def tool_execution_node",
    },
    "routing.supervisor": {
        "label": "Supervisor 路由与分支",
        "path": "mediZJ/lgraph/supervisor_graph.py",
        "symbol": "def _route_by_subtask_count",
    },
    "routing.decompose": {
        "label": "Lead Agent 任务分解",
        "path": "mediZJ/swarm/lead_agent.py",
        "symbol": "async def assess_and_decompose",
    },
    "memory.profile": {
        "label": "患者画像与个性化记忆",
        "path": "mediZJ/memory/personal_profile.py",
        "symbol": "class PersonalProfile",
    },
    "synthesis.graph": {
        "label": "多 Agent 结果综合",
        "path": "mediZJ/lgraph/supervisor_graph.py",
        "symbol": "async def _synthesize_results",
    },
    "coordinator.entry": {
        "label": "Swarm 请求处理入口",
        "path": "mediZJ/swarm/swarm_coordinator.py",
        "symbol": "async def process(",
    },
}

_ATTRIBUTION_SOURCES = {
    "prompt": ["prompt.lead_system", "prompt.assessment"],
    "retrieval": ["retrieval.memory", "retrieval.knowledge"],
    "tool_call": ["tool.registry", "tool.execution"],
    "routing": ["routing.supervisor", "routing.decompose"],
    "memory_profile": ["memory.profile", "retrieval.memory"],
    "synthesis": ["prompt.synthesis", "synthesis.graph"],
    "other": ["coordinator.entry"],
}


def get_source_locations(attributions: List[str]) -> List[Dict[str, Any]]:
    """把失败归因映射为可审计的源码位置。"""
    source_ids = []
    for attribution in attributions or ["other"]:
        for source_id in _ATTRIBUTION_SOURCES.get(
            attribution,
            _ATTRIBUTION_SOURCES["other"],
        ):
            if source_id not in source_ids:
                source_ids.append(source_id)
    locations = []
    for source_id in source_ids:
        entry = _SOURCES[source_id]
        line = _find_symbol_line(entry)
        locations.append(
            {
                "source_id": source_id,
                "label": entry["label"],
                "path": entry["path"],
                "symbol": entry["symbol"],
                "line": line,
            }
        )
    return locations


def read_source_snippet(source_id: str, radius: int = 18) -> Dict[str, Any]:
    """仅读取白名单中的源码片段。"""
    entry = _SOURCES.get(source_id)
    if entry is None:
        raise LookupError("源码位置不存在")
    path = (_PROJECT_ROOT / entry["path"]).resolve()
    if _PROJECT_ROOT not in path.parents or not path.is_file():
        raise LookupError("源码文件不存在")
    lines = path.read_text(encoding="utf-8").splitlines()
    target_line = _find_symbol_line(entry)
    start = max(1, target_line - radius)
    end = min(len(lines), target_line + radius)
    content = "\n".join(
        f"{line_number:>4} | {lines[line_number - 1]}"
        for line_number in range(start, end + 1)
    )
    return {
        "source_id": source_id,
        "label": entry["label"],
        "path": entry["path"],
        "symbol": entry["symbol"],
        "line": target_line,
        "start_line": start,
        "end_line": end,
        "content": content,
    }


def _find_symbol_line(entry: Dict[str, str]) -> int:
    path = _PROJECT_ROOT / entry["path"]
    if not path.is_file():
        return 1
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if entry["symbol"] in line:
            return line_number
    return 1
