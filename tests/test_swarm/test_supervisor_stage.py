"""test_swarm/test_supervisor_stage.py — 依赖性子问题（DAG 分层）主图测试

覆盖：
- 判定为 dag 后按依赖分层执行，后一阶段 worker 收到前一阶段结论
- 最终拼成一条含多个 "## " 小节的 final_answer
- plan_stages 返回 atomic / 抛错 / LeadAgent 无 plan_stages 时回落原子路径
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.core.llm_client import LLMResponse
from mediZJ.swarm.intent_classifier import IntentResult

DAG_QUESTION = (
    "某肿瘤最新治疗方案是什么？如果用这个方案出现不良反应怎么办？"
)


def _make_worker(agent_id: str, answer: str):
    """构造 mock Worker：记录传给 LLM 的 messages，并返回固定回答。"""
    worker = MagicMock()
    worker.agent_id = agent_id
    worker.config = {"max_iterations": 3, "temperature": 0.7}
    worker.short_term_memory = type("STM", (), {
        "get_history": AsyncMock(return_value=[]),
        "add_message": AsyncMock(return_value=None),
    })()
    worker.user_context = None
    worker.on_thinking = None
    worker.on_tool_step = None
    worker.on_thinking_done = None
    worker.on_content_token = None
    worker.calls: list = []

    def _response():
        return LLMResponse(
            content=answer,
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 5, "completion_tokens": 7,
                   "total_tokens": 12, "cached_prompt_tokens": 0},
        )

    def _record(messages=None, **kwargs):
        worker.calls.append(messages)
        return _response()

    worker.llm_client = MagicMock()
    worker.llm_client.chat_with_tools_stream = AsyncMock(side_effect=_record)
    worker.llm_client.chat_with_tools_retry = AsyncMock(side_effect=_record)
    worker.llm_client.chat_with_tools = AsyncMock(side_effect=_record)
    worker.get_base_system_prompt_stable = MagicMock(return_value="系统提示")
    worker.format_user_input = MagicMock(side_effect=lambda kw: kw["question"])
    worker.post_process_result = AsyncMock(side_effect=lambda r, content: r)
    return worker


def _messages_text(messages):
    """把 OpenAI messages 列表转成纯文本，便于断言注入内容。"""
    texts = []
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        if content:
            texts.append(str(content))
    return "\n".join(texts)


def _make_coordinator(lead_plan_response=None, has_plan_stages=True):
    """构造 mock coordinator（questionnaire_manager=None → 跳过澄清，直达 DAG）。"""
    coordinator = type("Coordinator", (), {})()
    coordinator.questionnaire_manager = None

    coordinator.short_term_memory = type("STM", (), {
        "get_recent_messages": AsyncMock(return_value=[]),
        "add_message": AsyncMock(return_value=None),
        "merge_sub_session": MagicMock(),
    })()
    coordinator.personal_profile = type("PP", (), {"to_text": lambda self: "暂无"})()
    coordinator.format_references_section = MagicMock(return_value="")
    coordinator.extract_suggestions = MagicMock(return_value=[])
    coordinator._save_session_summary = MagicMock()

    workers = {
        "consultation_agent": _make_worker(
            "consultation_agent", "针对该方案的不良反应，应密切监测并按指南分级处理。"),
        "diagnostic_agent": _make_worker("diagnostic_agent", "无需额外的诊断信息。"),
        "research_agent": _make_worker(
            "research_agent", "最新推荐方案：帕博利珠单抗联合化疗。"),
    }
    coordinator.workers = workers
    coordinator.get_worker = lambda agent_id: workers.get(agent_id)

    methods = {
        "agent_id": "lead_agent",
        "set_on_thinking": lambda self, cb: None,
        "set_on_thinking_done": lambda self, cb: None,
        "assess_and_decompose": AsyncMock(return_value={
            "subtasks": [{"description": "回答用户问题",
                          "assigned_agent": "consultation_agent"}],
        }),
    }
    if has_plan_stages:
        methods["plan_stages"] = AsyncMock(
            return_value=lead_plan_response
            if lead_plan_response is not None
            else {
                "mode": "dag",
                "reason": "含依赖子问",
                "stages": [
                    {"stage_id": "s1", "title": "最新治疗方案",
                     "question": "该肿瘤的最新治疗方案是什么？",
                     "description": "检索并给出含具体方案名的治疗方案结论",
                     "assigned_agent": "research_agent", "depends_on": []},
                    {"stage_id": "s2", "title": "该方案不良反应应对",
                     "question": "如果用这个方案出现不良反应怎么办？",
                     "description": "在阶段 s1 给出的具体方案基础上说明不良反应应对",
                     "assigned_agent": "consultation_agent", "depends_on": ["s1"]},
                ],
            },
        )
    coordinator.lead_agent = type("LA", (), methods)()

    coordinator.intent_classifier = type("IC", (), {
        "classify": AsyncMock(return_value=IntentResult(
            intent="medical", confidence=0.9, source="llm", reason="test",
        )),
    })()
    return coordinator


def _build_graph(coordinator):
    from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
    registry = MagicMock()
    registry.get_visible_tools = MagicMock(return_value=[])
    return build_supervisor_graph(coordinator, tool_registry=registry,
                                  hitl_enabled=False)


def _user_texts(worker) -> list:
    """提取该 worker 每次 LLM 调用中的全部 user 消息文本。"""
    out = []
    for messages in worker.calls:
        for m in messages or []:
            if (isinstance(m, dict) and m.get("role") == "user" and m.get("content")):
                out.append(str(m["content"]))
    return out


class TestDagResolution:
    @pytest.mark.asyncio
    async def test_chain_resolves_with_prereq_injection(self):
        """s1(方案) → s2(不良反应)：s2 的 worker 输入应包含 s1 的结论。"""
        coordinator = _make_coordinator()
        graph = _build_graph(coordinator)

        result = await graph.ainvoke({
            "question": DAG_QUESTION,
            "session_id": "s-dag",
        })

        research = coordinator.workers["research_agent"]
        consult = coordinator.workers["consultation_agent"]

        # 研究 worker：看到阶段1子问，不含前置结论
        research_user = "\n".join(_user_texts(research))
        assert "该肿瘤的最新治疗方案是什么" in research_user
        assert "## 前置结论" not in research_user

        # 咨询 worker：阶段2子问 + 前置结论（阶段1答案）
        consult_user = "\n".join(_user_texts(consult))
        assert "如果用这个方案出现不良反应怎么办" in consult_user
        assert "## 前置结论" in consult_user
        assert "帕博利珠单抗联合化疗" in consult_user  # 阶段1结论已喂入

        # 未走原子任务分解
        coordinator.lead_agent.assess_and_decompose.assert_not_awaited()

        # 单条分小节 final_answer
        assert "## 最新治疗方案" in result["final_answer"]
        assert "## 该方案不良反应应对" in result["final_answer"]
        assert result["mode"] == "swarm"
        assert result["swarm_metadata"]["num_stages"] == 2
        assert result["swarm_metadata"]["num_stages_completed"] == 2

    @pytest.mark.asyncio
    async def test_plan_returning_atomic_falls_back(self):
        """plan_stages 判 atomic → 走原 assess_decompose 路径。"""
        coordinator = _make_coordinator(lead_plan_response={
            "mode": "atomic", "reason": "可一次求解", "stages": [],
        })
        graph = _build_graph(coordinator)
        result = await graph.ainvoke({
            "question": DAG_QUESTION, "session_id": "s-atomic",
        })
        coordinator.lead_agent.assess_and_decompose.assert_awaited_once()
        assert result.get("final_answer")

    @pytest.mark.asyncio
    async def test_lead_without_plan_stages_falls_back(self):
        """旧式 LeadAgent（无 plan_stages）即使疑似多跳也不报错，走原子路径。"""
        coordinator = _make_coordinator(has_plan_stages=False)
        graph = _build_graph(coordinator)
        result = await graph.ainvoke({
            "question": DAG_QUESTION, "session_id": "s-legacy",
        })
        coordinator.lead_agent.assess_and_decompose.assert_awaited_once()
        assert result.get("final_answer")

    @pytest.mark.asyncio
    async def test_plan_stage_error_falls_back_to_atomic(self):
        """plan_stages 抛错 → 回落原子路径，不中断整图。"""

        def _boom(*a, **k):
            raise RuntimeError("planner boom")

        coordinator = _make_coordinator()
        coordinator.lead_agent.plan_stages = AsyncMock(side_effect=_boom)
        graph = _build_graph(coordinator)
        result = await graph.ainvoke({
            "question": DAG_QUESTION, "session_id": "s-err",
        })
        coordinator.lead_agent.assess_and_decompose.assert_awaited_once()
        assert result.get("final_answer")

    @pytest.mark.asyncio
    async def test_atomic_question_without_chain_skips_plan_llm(self):
        """不含回指链的单问：plan_stages 不应被调用（预筛短路）。"""
        coordinator = _make_coordinator()
        graph = _build_graph(coordinator)
        await graph.ainvoke({
            "question": "头疼两天了，应该注意什么？", "session_id": "s-simple",
        })
        coordinator.lead_agent.plan_stages.assert_not_awaited()
        coordinator.lead_agent.assess_and_decompose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dag_emits_stage_events_and_wave_progress(self):
        """流式：两个阶段的 SUBTASK 事件齐全，且向前端广播"分层求解推进"。"""
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
        from mediZJ.swarm.events import EventType

        coordinator = _make_coordinator()
        events: list = []
        registry = MagicMock()
        registry.get_visible_tools = MagicMock(return_value=[])
        graph = build_supervisor_graph(
            coordinator, tool_registry=registry,
            event_callback=events.append, hitl_enabled=False,
        )
        result = await graph.ainvoke({
            "question": DAG_QUESTION, "session_id": "s-dag-events",
        })

        completed_ids = {
            e.data.get("subtask_id")
            for e in events if e.type == EventType.SUBTASK_COMPLETED
        }
        assert completed_ids == {"s1", "s2"}

        progress = [
            e.data for e in events if e.type == EventType.AGENT_THINKING
            and e.data.get("phase") == "decompose"
            and e.data.get("title") == "分层求解推进"
        ]
        assert progress, "应广播分层求解推进事件"
        assert result["swarm_metadata"]["num_stages_completed"] == 2

    @pytest.mark.asyncio
    async def test_budget_exhausted_skips_remaining_and_returns_note(self, monkeypatch):
        """总预算耗尽（负数触发）：剩余层置 skipped，仍返回带说明的回答且不抛错。"""
        monkeypatch.setenv("STAGE_TOTAL_BUDGET", "-1")
        coordinator = _make_coordinator()
        graph = _build_graph(coordinator)

        result = await graph.ainvoke({
            "question": DAG_QUESTION, "session_id": "s-budget",
        })
        assert "未完成" in result["final_answer"]
        assert result["swarm_metadata"]["num_stages_completed"] == 0
        assert result["timeout_occurred"] is True
