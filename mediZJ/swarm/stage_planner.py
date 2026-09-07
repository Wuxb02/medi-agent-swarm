"""
阶段规划纯函数（无依赖、便于单测）

用于支持"同一条用户消息内的依赖性子问题（DAG 分层求解）"：
- looks_like_dependent_chain：代价极低的启发式预筛，命中才调用规划 LLM
- normalize_stage_plan：把 LLM 返回的阶段计划规范化（agent 白名单、去环、截断）
- topological_waves：依赖图的层波次划分（供单测与护栏）
- unify_stage_citations / replace_citation_refs：跨阶段引用统一重编号
"""
import re
from typing import Any, Dict, List, Optional

# 各 Agent 白名单（与 lgraph/worker.py 一致）
AGENT_WHITELIST = {
    "consultation_agent",
    "diagnostic_agent",
    "research_agent",
}

# 同消息内"回指上一子问产出的具体结论"的表达
_DEPENDENT_REF_RE = re.compile(
    r"(?:该|此|这种|这类|上述|这(?:种|类|套|个)?)\s*"
    r"(?:方案|治疗|疗法|药(?:物)?|用药|手术|方法|处理|药物组合)"
    r"|该药|这类药|这种药|该疗法|该术式|上述方案"
)
# 依赖成立的衔接/风险表达（决定第二个问句是否必须基于前一结论）
_CONNECTOR_RE = re.compile(
    r"如果|假如|要是|假设|倘若|那么|然后|之后|下一步|后续|"
    r"出现|发生|引起|副作用|不良反应|耐受|风险|禁忌|"
    r"怎么办|如何应对|怎么处理|该如何|还能不能|要不要|改用|换用|再用"
)

# 引用编号匹配：与 supervisor_graph._apply_renumber_to_contributions 保持一致
_CITATION_RE = re.compile(r"\[(\d+(?:[,\-]\d+)*)\]")

# 每阶段默认的执行说明
_DEFAULT_DESCRIPTION = "回答该子问题并给出明确、自包含的结论"


def looks_like_dependent_chain(question: str) -> bool:
    """启发式判断是否疑似含"同消息内依赖性子问"。

    仅当消息含至少两个问句，且同时出现"回指上一结论"与"衔接/风险"
    表达时才判真。判假只是跳过 LLM 规划调用（走现有原子路径，零额外开销）；
    判真后仍由规划 LLM 决定 atomic 还是 dag，因此该函数只需低误报、可略宽。
    """
    if not question or not isinstance(question, str):
        return False
    marks = sum(1 for ch in question if ch in "?？")
    if marks < 2:
        return False
    return bool(_DEPENDENT_REF_RE.search(question)
                and _CONNECTOR_RE.search(question))


def _dep_to_stage_id(dep: Any, prior_ids: List[str]) -> Optional[str]:
    """把 LLM 返回的依赖引用归一化为更早阶段的 stage_id。

    接受 "s1"/"S1" 或 1 基整数编号；只允许引用已出现过的阶段。
    """
    if isinstance(dep, int):
        sid = f"s{dep}"
    elif isinstance(dep, str):
        text = dep.strip().lower()
        if re.fullmatch(r"s\d+", text):
            sid = text
        elif re.fullmatch(r"\d+", text):
            sid = f"s{int(text)}"
        else:
            return None
    else:
        return None
    return sid if sid in prior_ids else None


def _atomic(issues: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"mode": "atomic", "stages": [], "issues": issues or []}


def normalize_stage_plan(raw: Any, max_stages: int = 4) -> Dict[str, Any]:
    """规范化规划 LLM 的输出。

    返回 {"mode": "atomic"|"dag", "stages": [...], "issues": [...]}。
    dag 模式下 stages 每项字段：stage_id/title/question/description/
    assigned_agent/depends_on/type。depends_on 只允许指向更早阶段（去环），
    超 max_stages 的阶段丢弃（连带其依赖一并移除）。
    """
    issues: List[str] = []
    if not isinstance(raw, dict):
        issues.append("plan_stages 输出非 dict")
        return _atomic(issues)

    if raw.get("mode") != "dag":
        return _atomic(issues)

    raw_stages = raw.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        issues.append("dag 模式未给出 stages，降级为 atomic")
        return _atomic(issues)

    stages: List[Dict[str, Any]] = []
    for i, item in enumerate(raw_stages[:max_stages]):
        if not isinstance(item, dict):
            issues.append(f"stage[{i}] 非 dict，已跳过")
            continue
        stage_id = f"s{i + 1}"
        prior_ids = [s["stage_id"] for s in stages]

        agent = item.get("assigned_agent")
        if agent not in AGENT_WHITELIST:
            if agent:
                issues.append(f"stage[{i}] assigned_agent={agent!r} 非法，回落 consultation_agent")
            agent = "consultation_agent"

        depends_on: List[str] = []
        raw_deps = item.get("depends_on") or []
        if not isinstance(raw_deps, list):
            raw_deps = [raw_deps]
        for dep in raw_deps:
            sid = _dep_to_stage_id(dep, prior_ids)
            if sid is None:
                issues.append(f"stage[{i}] depends_on={dep!r} 非法/未指向更早阶段，已忽略")
            elif sid not in depends_on:
                depends_on.append(sid)

        question = str(item.get("question") or "").strip()
        title = str(item.get("title") or f"阶段{i + 1}").strip()[:60]
        description = str(item.get("description") or "").strip() or _DEFAULT_DESCRIPTION

        stages.append({
            "stage_id": stage_id,
            "title": title or f"阶段{i + 1}",
            "question": question,
            "description": description,
            "assigned_agent": agent,
            "depends_on": depends_on,
            "type": str(item.get("type") or "stage"),
        })

    if not stages:
        issues.append("规范化后无有效阶段，降级为 atomic")
        return _atomic(issues)

    return {"mode": "dag", "stages": stages, "issues": issues}


def topological_waves(stages: List[Dict[str, Any]]) -> List[List[str]]:
    """按依赖关系把阶段划分为多个可并行的波次（返回 stage_id 列表的列表）。

    仅用于单测与可视化；运行时直接增量判定。若存在环/死锁，剩余阶段不会
    进入任何波次（返回值长度小于阶段数，调用方按死锁处理）。
    """
    by_id = {s["stage_id"]: s for s in stages}
    done = set()
    remaining = {s["stage_id"] for s in stages}
    waves: List[List[str]] = []

    while remaining:
        wave = [
            sid for sid in remaining
            if all(d in done for d in by_id[sid].get("depends_on", []))
        ]
        if not wave:
            break  # 环或死锁
        waves.append(wave)
        done.update(wave)
        remaining.difference_update(wave)
    return waves


def replace_citation_refs(text: str, mapping: Dict[int, int]) -> str:
    """把文本中的 [N]/[N,M]/[N-M] 按 old->new 映射替换（无映射段原样保留）。"""
    if not mapping or not text:
        return text

    def _sub(match: "re.Match") -> str:
        nums_str = match.group(1)
        parts = re.split(r"([,\-])", nums_str)
        new_parts = []
        for part in parts:
            if part in (",", "-"):
                new_parts.append(part)
            else:
                try:
                    new_parts.append(str(mapping.get(int(part), int(part))))
                except ValueError:
                    new_parts.append(part)
        return "[" + "".join(new_parts) + "]"

    return _CITATION_RE.sub(_sub, text)


def unify_stage_citations(
    entries: List[Dict[str, Any]],
) -> tuple[List[str], List[Dict[str, Any]]]:
    """跨阶段统一引用：按 doc_id 全局去重、按首次出现重编号。

    Args:
        entries: 按执行顺序排列的阶段条目，每项含
            {"stage_id", "text", "references"}；references 内每项含
            doc_id 与该文本内的本地 index。

    Returns:
        (renumbered_texts, citations)：texts 与 entries 顺序一一对应，
        文本中的旧 [n] 已替换为全局编号；citations 为去重后的全局引用
        （index 已重编号为全局序号）。
    """
    doc_to_ref: Dict[str, Dict[str, Any]] = {}
    doc_order: List[str] = []
    per_stage: List[List[tuple]] = []  # 每阶段 [(local_index, doc_id)]

    for entry in entries:
        stage_refs: List[tuple] = []
        for ref in entry.get("references") or []:
            if not isinstance(ref, dict):
                continue
            doc_id = ref.get("doc_id", "")
            local_index = ref.get("index", 0)
            if not doc_id or doc_id in doc_to_ref:
                # 已见过的 doc 无需重复收集，但仍记录该阶段对它的引用
                if doc_id:
                    stage_refs.append((local_index, doc_id))
                continue
            doc_to_ref[doc_id] = dict(ref)
            doc_order.append(doc_id)
            stage_refs.append((local_index, doc_id))
        per_stage.append(stage_refs)

    doc_to_new = {doc_id: idx + 1 for idx, doc_id in enumerate(doc_order)}
    citations: List[Dict[str, Any]] = []
    for idx, doc_id in enumerate(doc_order, 1):
        ref_copy = dict(doc_to_ref[doc_id])
        ref_copy["index"] = idx
        citations.append(ref_copy)

    renumbered_texts: List[str] = []
    for entry, stage_refs in zip(entries, per_stage):
        mapping = {
            local_index: doc_to_new[doc_id]
            for local_index, doc_id in stage_refs
            if doc_id in doc_to_new
        }
        renumbered_texts.append(
            replace_citation_refs(entry.get("text", ""), mapping)
        )
    return renumbered_texts, citations
