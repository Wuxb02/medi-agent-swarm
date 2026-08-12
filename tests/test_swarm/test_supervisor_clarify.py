"""test_swarm/test_supervisor_clarify.py — clarify 问卷 interrupt/Command/checkpoint 测试

覆盖：
- 主图 clarify 单轮 interrupt：含 questionnaire_manager 的图 ainvoke 后挂起，
  checkpoint 保存 interrupt 状态（questionnaire_id / questionnaire_data）
- Command(resume=...) 二次 ainvoke 恢复，collected_info 注入 assess_decompose
- 工具权限收口：question_for_user 仅 LeadAgent 可见，Worker 不可见
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.core.llm_client import LLMResponse, ToolCall
from mediZJ.lgraph.tool_registry import ToolRegistry
from mediZJ.swarm.intent_classifier import IntentResult


def _make_worker(agent_id: str):
    """构造 mock Worker：带 short_term_memory + llm_client（子图执行依赖）"""
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

    # LLM：流式/非流式均返回最终回答（无工具调用）
    from mediZJ.core.llm_client import LLMResponse
    final_response = LLMResponse(
        content="根据您的描述，建议尽快就医。",
        tool_calls=[],
        finish_reason="stop",
        usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    )
    worker.llm_client = MagicMock()
    worker.llm_client.chat_with_tools_stream = AsyncMock(return_value=final_response)
    worker.llm_client.chat_with_tools_retry = AsyncMock(return_value=final_response)
    worker.llm_client.chat_with_tools = AsyncMock(return_value=final_response)
    worker.post_process_result = AsyncMock(side_effect=lambda r, content: r)
    worker.get_base_system_prompt_stable = MagicMock(return_value="系统提示")
    worker.format_user_input = MagicMock(side_effect=lambda kw: kw["question"])
    return worker


def _make_coordinator(questionnaire_manager, lead_llm_response):
    """构造 mock coordinator：问卷管理器 + LeadAgent LLM 响应可控"""
    coordinator = type("Coordinator", (), {})()

    # 问卷管理器（None 表示未配置，clarify 跳过）
    coordinator.questionnaire_manager = questionnaire_manager

    # 记忆 / 档案
    coordinator.short_term_memory = type("STM", (), {
        "get_recent_messages": AsyncMock(return_value=[]),
        "add_message": AsyncMock(return_value=None),
        "merge_sub_session": MagicMock(),
    })()
    coordinator.long_term_memory = type("LTM", (), {
        "search_similar_sessions": AsyncMock(return_value=[]),
    })()
    coordinator.personal_profile = type("PP", (), {"to_text": lambda self: "暂无"})()
    coordinator._refresh_worker_profiles = lambda *args, **kwargs: None
    coordinator._save_long_term_memory = AsyncMock(return_value=None)
    coordinator._save_session_summary = MagicMock()
    coordinator.format_references_section = MagicMock(return_value="")
    coordinator.extract_suggestions = MagicMock(return_value=[])

    # Worker（build_supervisor_graph 通过 get_worker 访问）
    _workers = {
        "consultation_agent": _make_worker("consultation_agent"),
        "diagnostic_agent": _make_worker("diagnostic_agent"),
        "research_agent": _make_worker("research_agent"),
    }
    coordinator.get_worker = lambda agent_id: _workers.get(agent_id)

    # LeadAgent：澄清 LLM 响应可控（支持 side_effect 序列实现多轮）
    llm_client = MagicMock()
    if isinstance(lead_llm_response, list):
        llm_client.chat_with_tools = AsyncMock(side_effect=lead_llm_response)
        llm_client.chat_with_tools_stream = AsyncMock(side_effect=lead_llm_response)
    else:
        llm_client.chat_with_tools = AsyncMock(return_value=lead_llm_response)
        llm_client.chat_with_tools_stream = AsyncMock(return_value=lead_llm_response)
    coordinator.lead_agent = type("LA", (), {
        "agent_id": "lead_agent",
        "llm_client": llm_client,
        "_get_clarify_system_prompt": lambda self: "clarify system prompt",
        "assess_and_decompose": AsyncMock(return_value={
            "subtasks": [{"description": "回答用户问题",
                          "assigned_agent": "consultation_agent"}],
        }),
        "set_on_thinking": lambda *a, **k: None,
        "set_on_thinking_done": lambda *a, **k: None,
    })()

    # 意图分类
    coordinator.intent_classifier = type("IC", (), {
        "classify": AsyncMock(return_value=IntentResult(
            intent="medical", confidence=0.9, source="llm", reason="test",
        )),
    })()
    return coordinator


def _questionnaire_response():
    """LeadAgent clarify LLM 返回 question_for_user 工具调用"""
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(
            id="call_q1",
            name="question_for_user",
            arguments={"questionnaire": (
                "<questions>"
                "<question header='年龄' type='input'><text>您的年龄是？</text></question>"
                "</questions>"
            )},
        )],
        finish_reason="tool_calls",
    )


def _no_clarify_response():
    """LeadAgent clarify LLM 判定无需澄清"""
    return LLMResponse(
        content="无需额外信息",
        tool_calls=[],
        finish_reason="stop",
    )


class TestClarifyInterrupt:
    """主图 clarify 单轮 interrupt + Command resume"""

    @pytest.mark.asyncio
    async def test_clarify_interrupts_and_resumes(self):
        """含 questionnaire_manager 的图：clarify 发出问卷后 interrupt 挂起；
        Command(resume) 恢复后 collected_info 注入 assess_decompose。"""
        from mediZJ.api.services.session_runtime import (
            clear_answer_queue, release_runtime,
        )

        manager = MagicMock()
        # 第一轮发问卷，resume 后第二轮判定无需澄清 → 结束澄清
        coordinator = _make_coordinator(
            manager,
            [_questionnaire_response(), _no_clarify_response()],
        )
        registry = MagicMock()
        registry.get_visible_tools = MagicMock(return_value=[])
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
        graph = build_supervisor_graph(coordinator, tool_registry=registry,
                                       hitl_enabled=True)
        config = {"configurable": {"thread_id": "s-clarify"}}

        # 首次执行：clarify_ask 节点 interrupt 挂起
        result = await graph.ainvoke(
            {"question": "头痛还恶心，怎么回事", "session_id": "s-clarify"},
            config,
        )

        # LangGraph 1.x：ainvoke 返回含 __interrupt__ 的状态 dict（挂起态）
        assert "__interrupt__" in result
        interrupt = result["__interrupt__"][0]
        payload = interrupt.value
        assert payload["type"] == "questionnaire"
        assert payload["questionnaire_id"]
        assert "questions" in payload["questionnaire_data"]

        # Command(resume=...) 恢复：用户提交答案
        from langgraph.types import Command
        resumed = await graph.ainvoke(Command(resume={"q0": "35"}), config)

        # 恢复后图继续执行：assess_and_decompose 收到 collected_info
        coordinator.lead_agent.assess_and_decompose.assert_awaited_once()
        assess_kwargs = coordinator.lead_agent.assess_and_decompose.call_args.kwargs
        context = assess_kwargs.get("context", {})
        assert "35" in context.get("collected_info", "")
        assert resumed.get("final_answer") is not None

        clear_answer_queue("s-clarify")
        release_runtime("s-clarify")

    @pytest.mark.asyncio
    async def test_clarify_emits_lead_reasoning_and_questionnaire_tool_steps(self):
        """流式澄清应输出 LeadAgent 思考、问卷等待与已回答状态。"""
        from langgraph.types import Command

        from mediZJ.api.services.session_runtime import (
            clear_answer_queue,
            release_runtime,
        )
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph

        events = []
        coordinator = _make_coordinator(
            MagicMock(),
            [_questionnaire_response(), _no_clarify_response()],
        )
        registry = MagicMock()
        registry.get_visible_tools = MagicMock(return_value=[])
        graph = build_supervisor_graph(
            coordinator,
            tool_registry=registry,
            event_callback=events.append,
            hitl_enabled=True,
        )
        config = {"configurable": {"thread_id": "s-clarify-events"}}

        first = await graph.ainvoke(
            {"question": "头痛还恶心，怎么回事", "session_id": "s-clarify-events"},
            config,
        )
        assert "__interrupt__" in first
        await graph.ainvoke(Command(resume={"q0": "35"}), config)

        clarify_thinking = [
            event for event in events
            if event.type.value == "agent_thinking"
            and event.data.get("phase") == "clarify"
        ]
        questionnaire_steps = [
            event for event in events
            if event.type.value == "agent_tool_step"
            and event.data.get("tool_name") == "question_for_user"
        ]

        assert clarify_thinking
        assert {step.data["status"] for step in questionnaire_steps} == {
            "waiting",
            "completed",
        }
        completed_step = next(
            step for step in questionnaire_steps
            if step.data["status"] == "completed"
        )
        assert "年龄: 35" in completed_step.data["result"]

        clear_answer_queue("s-clarify-events")
        release_runtime("s-clarify-events")

    @pytest.mark.asyncio
    async def test_clarify_multi_round_follow_up(self):
        """LLM 判定需要追问：连续两轮问卷，两次 interrupt，collected_info 汇总两轮答案。"""
        from mediZJ.api.services.session_runtime import (
            clear_answer_queue, release_runtime,
        )

        manager = MagicMock()
        # 三轮 LLM：发问卷 → 发问卷 → 无需澄清
        coordinator = _make_coordinator(
            manager,
            [_questionnaire_response(), _questionnaire_response(), _no_clarify_response()],
        )
        registry = MagicMock()
        registry.get_visible_tools = MagicMock(return_value=[])
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
        graph = build_supervisor_graph(coordinator, tool_registry=registry,
                                       hitl_enabled=True)
        config = {"configurable": {"thread_id": "s-multi"}}

        # 第 1 轮 interrupt
        result = await graph.ainvoke(
            {"question": "头痛还恶心，怎么回事", "session_id": "s-multi"},
            config,
        )
        assert "__interrupt__" in result
        qid1 = result["__interrupt__"][0].value["questionnaire_id"]

        from langgraph.types import Command
        # 第 1 轮 resume → 又发第 2 份问卷 → 再 interrupt
        result2 = await graph.ainvoke(Command(resume={"q0": "35"}), config)
        assert "__interrupt__" in result2
        qid2 = result2["__interrupt__"][0].value["questionnaire_id"]
        assert qid2 != qid1  # 每轮问卷 id 唯一

        # 第 2 轮 resume → LLM 判定无需澄清 → 结束，collected_info 含两轮答案
        resumed = await graph.ainvoke(Command(resume={"q0": "头痛一天"}), config)
        assert "__interrupt__" not in resumed

        coordinator.lead_agent.assess_and_decompose.assert_awaited_once()
        context = coordinator.lead_agent.assess_and_decompose.call_args.kwargs.get("context", {})
        collected = context.get("collected_info", "")
        assert "35" in collected       # 第一轮答案（年龄）
        assert "头痛一天" in collected  # 第二轮答案（症状描述）

        clear_answer_queue("s-multi")
        release_runtime("s-multi")

    @pytest.mark.asyncio
    async def test_clarify_hard_cap_three_rounds(self):
        """硬上限 3 轮：LLM 每轮都发问卷，第 4 次 LLM 不会被调用。"""
        from mediZJ.api.services.session_runtime import (
            clear_answer_queue, release_runtime,
        )

        manager = MagicMock()
        # 每轮都返回问卷（模拟 LLM 永不满意）
        coordinator = _make_coordinator(manager, _questionnaire_response())
        registry = MagicMock()
        registry.get_visible_tools = MagicMock(return_value=[])
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
        graph = build_supervisor_graph(coordinator, tool_registry=registry,
                                       hitl_enabled=True)
        config = {"configurable": {"thread_id": "s-cap"}}
        from langgraph.types import Command

        # 3 次 interrupt（每轮一次）
        for i in range(3):
            result = await graph.ainvoke(
                {"question": "头痛还恶心，怎么回事", "session_id": "s-cap"},
                config,
            ) if i == 0 else await graph.ainvoke(Command(resume={"q0": f"ans{i}"}), config)
            assert "__interrupt__" in result

        # 第 4 次执行：已到硬上限，不再调 LLM，直接完成
        final = await graph.ainvoke(Command(resume={"q0": "ans3"}), config)
        assert "__interrupt__" not in final
        # LLM 只被调用 3 次（decide 每轮一次，硬上限后不再调）
        assert coordinator.lead_agent.llm_client.chat_with_tools.await_count == 3

        clear_answer_queue("s-cap")
        release_runtime("s-cap")

    @pytest.mark.asyncio
    async def test_clarify_before_retrieve_memories(self):
        """节点顺序：medical 意图下，记忆检索发生在 clarify 完成后、任务分解之前。"""
        from mediZJ.api.services.session_runtime import (
            clear_answer_queue, release_runtime,
        )

        manager = MagicMock()
        coordinator = _make_coordinator(
            manager,
            [_questionnaire_response(), _no_clarify_response()],
        )
        registry = MagicMock()
        registry.get_visible_tools = MagicMock(return_value=[])
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
        graph = build_supervisor_graph(coordinator, tool_registry=registry,
                                       hitl_enabled=True)
        config = {"configurable": {"thread_id": "s-order"}}
        from langgraph.types import Command

        # 首次：interrupt 挂起（尚未检索记忆）
        result = await graph.ainvoke(
            {"question": "头痛还恶心，怎么回事", "session_id": "s-order"},
            config,
        )
        assert "__interrupt__" in result
        # 首次挂起时不应已检索记忆 / 不应已任务分解
        coordinator.short_term_memory.get_recent_messages.assert_not_awaited()
        coordinator.lead_agent.assess_and_decompose.assert_not_awaited()

        # resume → 澄清完成 → 才检索记忆 → 任务分解
        final = await graph.ainvoke(Command(resume={"q0": "35"}), config)
        assert "__interrupt__" not in final
        coordinator.short_term_memory.get_recent_messages.assert_awaited()
        coordinator.lead_agent.assess_and_decompose.assert_awaited_once()

        clear_answer_queue("s-order")
        release_runtime("s-order")

    @pytest.mark.asyncio
    async def test_clarify_skipped_when_no_questionnaire_manager(self):
        """无 questionnaire_manager：clarify 直接跳过，不走 interrupt"""
        coordinator = _make_coordinator(None, _questionnaire_response())
        registry = MagicMock()
        registry.get_visible_tools = MagicMock(return_value=[])
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
        graph = build_supervisor_graph(coordinator, tool_registry=registry,
                                       hitl_enabled=True)
        config = {"configurable": {"thread_id": "s-skip"}}

        result = await graph.ainvoke(
            {"question": "头痛还恶心，怎么回事", "session_id": "s-skip"},
            config,
        )
        # 无 interrupt 挂起：直接到达最终回答
        assert "__interrupt__" not in result
        assert result.get("final_answer") is not None

    @pytest.mark.asyncio
    async def test_clarify_skipped_when_llm_judges_no_clarification(self):
        """LeadAgent 判定无需澄清：不发问卷、不 interrupt"""
        manager = MagicMock()
        coordinator = _make_coordinator(manager, _no_clarify_response())
        registry = MagicMock()
        registry.get_visible_tools = MagicMock(return_value=[])
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
        graph = build_supervisor_graph(coordinator, tool_registry=registry,
                                       hitl_enabled=True)
        config = {"configurable": {"thread_id": "s-nocl"}}

        result = await graph.ainvoke(
            {"question": "感冒了怎么办", "session_id": "s-nocl"},
            config,
        )
        assert "__interrupt__" not in result
        assert result.get("final_answer") is not None
        # 未触发问卷 → assess 的 collected_info 为空
        assess_kwargs = coordinator.lead_agent.assess_and_decompose.call_args.kwargs
        assert assess_kwargs.get("context", {}).get("collected_info", "") == ""

    @pytest.mark.asyncio
    async def test_non_hitl_graph_has_no_interrupt(self):
        """hitl_enabled=False：图不挂载 checkpointer，clarify 跳过，无 interrupt"""
        manager = MagicMock()
        coordinator = _make_coordinator(manager, _questionnaire_response())
        registry = MagicMock()
        registry.get_visible_tools = MagicMock(return_value=[])
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
        graph = build_supervisor_graph(coordinator, tool_registry=registry,
                                       hitl_enabled=False)
        config = {"configurable": {"thread_id": "s-nohitl"}}

        result = await graph.ainvoke(
            {"question": "头痛还恶心，怎么回事", "session_id": "s-nohitl"},
            config,
        )
        assert "__interrupt__" not in result
        assert result.get("final_answer") is not None


class TestQuestionToolVisibility:
    """question_for_user 仅 LeadAgent 可见（Worker 不可见）"""

    def _build_registry(self):
        registry = ToolRegistry()

        async def _activate_skill(name: str) -> dict:
            return {"success": True}

        async def _question(questionnaire: str) -> dict:
            return {"needs_user_input": True}

        registry.register_base_tool(
            name="activate_skill",
            func=_activate_skill,
            description="激活 Skill",
        )
        registry.register_base_tool(
            name="question_for_user",
            func=_question,
            description="向用户发送结构化问卷",
            allowed_agents=["lead_agent"],
        )
        return registry

    def test_question_for_user_visible_only_to_lead_agent(self):
        registry = self._build_registry()

        lead_tools = registry.get_visible_tools(active_skill=None, agent_id="lead_agent")
        lead_names = {t["function"]["name"] for t in lead_tools}
        assert "question_for_user" in lead_names
        assert "activate_skill" in lead_names

        worker_tools = registry.get_visible_tools(active_skill=None, agent_id="consultation_agent")
        worker_names = {t["function"]["name"] for t in worker_tools}
        assert "question_for_user" not in worker_names
        assert "activate_skill" in worker_names

    def test_question_for_user_visible_without_agent_filter(self):
        """未传 agent_id（None）时 question_for_user 不可见（需明确 LeadAgent 身份）"""
        registry = self._build_registry()
        tools = registry.get_visible_tools(active_skill=None, agent_id=None)
        names = {t["function"]["name"] for t in tools}
        assert "question_for_user" not in names
        assert "activate_skill" in names
