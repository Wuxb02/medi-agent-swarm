"""test_swarm/test_stage_planning.py — 阶段规划纯函数单测

覆盖 stage_planner：依赖链启发式、规范化（去环/白名单/截断）、拓扑层波次、
跨阶段引用统一重编号。
"""

from mediZJ.swarm.stage_planner import (
    looks_like_dependent_chain,
    normalize_stage_plan,
    replace_citation_refs,
    topological_waves,
    unify_stage_citations,
)


class TestLooksLikeDependentChain:
    def test_positive_dependent_questions(self):
        positives = [
            "xx 病最新治疗方案是什么？如果用这个方案出现不良反应怎么办？",
            "高血压目前首选药物有哪些？上述方案长期服用的主要副作用是什么？",
            "孩子发烧了可以先吃退烧药吗？如果吃了这个药还反复发热该怎么办？",
        ]
        for q in positives:
            assert looks_like_dependent_chain(q), q

    def test_negative_independent_or_single_questions(self):
        negatives = [
            "头疼怎么办？发烧怎么办？",          # 并列无依赖
            "我最近头晕，应该注意什么？",          # 单问
            "糖尿病饮食要注意什么？",
            "你好，帮我看看这个药",               # 单问
            "医生推荐的这个方案可以长期用吗？",    # 单问（回指但不构成链）
        ]
        for q in negatives:
            assert not looks_like_dependent_chain(q), q

    def test_empty_or_non_string(self):
        assert not looks_like_dependent_chain("")
        assert not looks_like_dependent_chain(None)


class TestNormalizeStagePlan:
    def test_valid_dag_keeps_order_and_deps(self):
        raw = {
            "mode": "dag",
            "reason": "测试",
            "stages": [
                {"stage_id": "s1", "title": "治疗方案", "question": "q1",
                 "description": "给出具体方案", "assigned_agent": "research_agent",
                 "depends_on": []},
                {"stage_id": "s2", "title": "不良反应", "question": "q2",
                 "description": "基于 s1", "assigned_agent": "consultation_agent",
                 "depends_on": ["s1"]},
            ],
        }
        plan = normalize_stage_plan(raw)
        assert plan["mode"] == "dag"
        assert [s["stage_id"] for s in plan["stages"]] == ["s1", "s2"]
        assert plan["stages"][1]["depends_on"] == ["s1"]
        assert all(k in plan["stages"][0] for k in (
            "stage_id", "title", "question", "description", "assigned_agent",
            "depends_on", "type"))

    def test_invalid_agent_falls_back(self):
        raw = {
            "mode": "dag",
            "stages": [
                {"title": "a", "assigned_agent": "evil_agent", "depends_on": []},
            ],
        }
        plan = normalize_stage_plan(raw)
        assert plan["stages"][0]["assigned_agent"] == "consultation_agent"

    def test_removes_self_forward_cycle_and_unknown_deps(self):
        raw = {
            "mode": "dag",
            "stages": [
                {"title": "a", "assigned_agent": "consultation_agent",
                 "depends_on": ["s2"]},   # 引用未出现（后方）→ 丢弃
                {"title": "b", "assigned_agent": "consultation_agent",
                 "depends_on": ["s1"]},   # 引用前方 → 保留
                {"title": "c", "assigned_agent": "consultation_agent",
                 "depends_on": ["s9"]},   # 不存在 → 丢弃
            ],
        }
        plan = normalize_stage_plan(raw)
        stages = plan["stages"]
        assert stages[0]["depends_on"] == []
        assert stages[1]["depends_on"] == ["s1"]
        assert stages[2]["depends_on"] == []
        # 任何依赖都只能指向更早阶段（无环）
        for i, s in enumerate(stages):
            prior = {x["stage_id"] for x in stages[:i]}
            assert set(s["depends_on"]) <= prior

    def test_truncates_beyond_max(self):
        raw = {
            "mode": "dag",
            "stages": [
                {"title": f"t{i}", "assigned_agent": "consultation_agent",
                 "depends_on": []}
                for i in range(6)
            ],
        }
        plan = normalize_stage_plan(raw, max_stages=4)
        assert len(plan["stages"]) == 4

    def test_invalid_inputs_fall_back_to_atomic(self):
        assert normalize_stage_plan(None)["mode"] == "atomic"
        assert normalize_stage_plan({"mode": "dag", "stages": []})["mode"] == "atomic"
        assert normalize_stage_plan({"mode": "atomic"})["mode"] == "atomic"
        assert normalize_stage_plan("nonsense")["mode"] == "atomic"
        # 全非法阶段（非 dict）→ atomic
        plan = normalize_stage_plan({"mode": "dag", "stages": ["x", 1]})
        assert plan["mode"] == "atomic"

    def test_numeric_deps_1based(self):
        raw = {
            "mode": "dag",
            "stages": [
                {"title": "a", "assigned_agent": "consultation_agent", "depends_on": []},
                {"title": "b", "assigned_agent": "consultation_agent", "depends_on": [1]},
            ],
        }
        plan = normalize_stage_plan(raw)
        assert plan["stages"][1]["depends_on"] == ["s1"]


class TestTopologicalWaves:
    def test_chain(self):
        plan = normalize_stage_plan({
            "mode": "dag",
            "stages": [
                {"title": "a", "assigned_agent": "consultation_agent", "depends_on": []},
                {"title": "b", "assigned_agent": "consultation_agent", "depends_on": ["s1"]},
            ],
        })
        assert topological_waves(plan["stages"]) == [["s1"], ["s2"]]

    def test_diamond(self):
        plan = normalize_stage_plan({
            "mode": "dag",
            "stages": [
                {"title": "a", "assigned_agent": "consultation_agent", "depends_on": []},
                {"title": "b", "assigned_agent": "consultation_agent", "depends_on": ["s1"]},
                {"title": "c", "assigned_agent": "consultation_agent", "depends_on": ["s1"]},
                {"title": "d", "assigned_agent": "consultation_agent",
                 "depends_on": ["s2", "s3"]},
            ],
        })
        waves = topological_waves(plan["stages"])
        assert waves[0] == ["s1"]
        assert set(waves[1]) == {"s2", "s3"}
        assert waves[2] == ["s4"]

    def test_deadlock_leftover_excluded(self):
        # 只有环 → 无任何可执行波
        assert topological_waves([
            {"stage_id": "s1", "depends_on": ["s2"]},
            {"stage_id": "s2", "depends_on": ["s1"]},
        ]) == []


class TestCitationRenumber:
    def test_replace_single_and_ranges(self):
        assert replace_citation_refs("见[1]与[2,3]，亦见[4-6]", {1: 5, 3: 7}) == "见[5]与[2,7]，亦见[4-6]"

    def test_unify_across_stages(self):
        entries = [
            {"stage_id": "s1", "text": "方案推荐 A[1] 或 B[2]。", "references": [
                {"doc_id": "d1", "index": 1}, {"doc_id": "d2", "index": 2}]},
            {"stage_id": "s2", "text": "对 A[1] 的不良反应见[1]，另参考[2]。", "references": [
                {"doc_id": "d1", "index": 1}, {"doc_id": "d3", "index": 2}]},
        ]
        texts, citations = unify_stage_citations(entries)
        assert len(citations) == 3
        assert [c["index"] for c in citations] == [1, 2, 3]
        assert texts[0] == "方案推荐 A[1] 或 B[2]。"
        # d1 → 1（去重），d3 → 3
        assert texts[1] == "对 A[1] 的不良反应见[1]，另参考[3]。"
