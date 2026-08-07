"""test_swarm/test_intent_classifier.py — 意图识别模块测试

覆盖：LLM 层分类、异常降级、图节点门控生效。
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from mediZJ.swarm.intent_classifier import IntentClassifier, IntentResult
from tests.helpers import make_mock_openai_response


def _set_llm_response(mock_llm_client, payload: dict) -> None:
    """让 mock_llm_client.chat 返回指定 JSON 负载。"""
    mock_llm_client.client.chat.completions.create = AsyncMock(
        return_value=make_mock_openai_response(content=json.dumps(payload, ensure_ascii=False))
    )


class TestIntentClassifierLLM:
    """LLM 层分类行为。"""

    @pytest.mark.asyncio
    async def test_others_skips_long_term(self, mock_llm_client):
        _set_llm_response(mock_llm_client, {"intent": "others", "confidence": 0.95, "reason": "寒暄"})
        classifier = IntentClassifier(llm_client=mock_llm_client)
        result = await classifier.classify("你好")
        assert result.intent == "others"
        assert result.source == "llm"
        assert result.skip_long_term is True

    @pytest.mark.asyncio
    async def test_medical_does_not_skip(self, mock_llm_client):
        _set_llm_response(mock_llm_client, {"intent": "medical", "confidence": 0.98, "reason": "症状咨询"})
        classifier = IntentClassifier(llm_client=mock_llm_client)
        result = await classifier.classify("我头痛怎么办")
        assert result.intent == "medical"
        assert result.skip_long_term is False

    @pytest.mark.asyncio
    async def test_composite_greeting_with_medical_does_not_skip(self, mock_llm_client):
        # 寒暄开头 + 医疗诉求 → medical，不跳过
        _set_llm_response(mock_llm_client, {"intent": "medical", "confidence": 0.9, "reason": "寒暄开头但含医疗诉求"})
        classifier = IntentClassifier(llm_client=mock_llm_client)
        result = await classifier.classify("你好，我最近头晕")
        assert result.intent == "medical"
        assert result.skip_long_term is False

    @pytest.mark.asyncio
    async def test_confidence_clamped_to_range(self, mock_llm_client):
        _set_llm_response(mock_llm_client, {"intent": "medical", "confidence": 1.5})
        result = await IntentClassifier(llm_client=mock_llm_client).classify("测试")
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_prompt_rendered_with_question(self, mock_llm_client):
        create_mock = AsyncMock(return_value=make_mock_openai_response(content=json.dumps({"intent": "medical"})))
        mock_llm_client.client.chat.completions.create = create_mock
        await IntentClassifier(llm_client=mock_llm_client).classify("我肚子疼")
        call_args = create_mock.call_args
        assert call_args.kwargs["temperature"] == 0
        assert call_args.kwargs["response_format"] == {"type": "json_object"}
        user_prompt = call_args.kwargs["messages"][1]["content"]
        assert "我肚子疼" in user_prompt


class TestIntentClassifierFallback:
    """异常降级：一律 medical（不跳过），source=fallback。"""

    @pytest.mark.asyncio
    async def test_invalid_json_falls_back_to_medical(self, mock_llm_client):
        mock_llm_client.client.chat.completions.create = AsyncMock(
            return_value=make_mock_openai_response(content="not-a-json")
        )
        result = await IntentClassifier(llm_client=mock_llm_client).classify("你好")
        assert result.intent == "medical"
        assert result.source == "fallback"
        assert result.skip_long_term is False

    @pytest.mark.asyncio
    async def test_timeout_falls_back_to_medical(self, mock_llm_client):
        async def _slow(*args, **kwargs):
            await asyncio.sleep(10)

        mock_llm_client.client.chat.completions.create = _slow
        classifier = IntentClassifier(llm_client=mock_llm_client, timeout=0.01)
        result = await classifier.classify("你好")
        assert result.intent == "medical"
        assert result.source == "fallback"

    @pytest.mark.asyncio
    async def test_network_error_falls_back_to_medical(self, mock_llm_client):
        mock_llm_client.client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("network down")
        )
        result = await IntentClassifier(llm_client=mock_llm_client).classify("你好")
        assert result.intent == "medical"
        assert result.source == "fallback"

    @pytest.mark.asyncio
    async def test_unknown_intent_normalized_to_medical(self, mock_llm_client):
        _set_llm_response(mock_llm_client, {"intent": "chitchat"})  # 未知值 → 保守 medical
        result = await IntentClassifier(llm_client=mock_llm_client).classify("你好")
        assert result.intent == "medical"
        assert result.source == "fallback"
        assert result.skip_long_term is False


class TestIntentResult:
    """IntentResult 派生属性。"""

    def test_skip_long_term_only_for_others(self):
        assert IntentResult(intent="others", confidence=0.9, source="llm").skip_long_term is True
        assert IntentResult(intent="medical", confidence=0.9, source="llm").skip_long_term is False


class TestRetrieveMemoriesGate:
    """门控函数级测试：意图门控是否真正生效（mock coordinator）。"""

    def _make_coordinator(self, intent: str):
        coordinator = type("Coordinator", (), {})()
        coordinator.short_term_memory = type("STM", (), {
            "get_recent_messages": AsyncMock(return_value=[{"role": "user", "content": "hi"}]),
        })()
        coordinator.long_term_memory = type("LTM", (), {
            "search_similar_sessions": AsyncMock(return_value=[{"memory_id": "m1"}]),
        })()
        coordinator.personal_profile = type("PP", (), {"to_text": lambda self: "男，30岁"})()
        coordinator.intent_classifier = type("IC", (), {
            "classify": AsyncMock(return_value=IntentResult(
                intent=intent, confidence=0.9, source="llm", reason="test",
            )),
        })()
        return coordinator

    @pytest.mark.asyncio
    async def test_others_skips_mem0(self):
        from mediZJ.lgraph.supervisor_graph import retrieve_memories_with_intent_gate

        coordinator = self._make_coordinator(intent="others")
        result = await retrieve_memories_with_intent_gate(
            coordinator,
            session_id="s1",
            question="你好",
            intent="others",
        )

        coordinator.long_term_memory.search_similar_sessions.assert_not_awaited()
        coordinator.short_term_memory.get_recent_messages.assert_awaited_once()
        assert result["skip_long_term_retrieval"] is True
        assert result["similar_memories"] == []

    @pytest.mark.asyncio
    async def test_medical_calls_mem0(self):
        from mediZJ.lgraph.supervisor_graph import retrieve_memories_with_intent_gate

        coordinator = self._make_coordinator(intent="medical")
        result = await retrieve_memories_with_intent_gate(
            coordinator,
            session_id="s2",
            question="我头痛",
            intent="medical",
        )

        coordinator.long_term_memory.search_similar_sessions.assert_awaited_once()
        assert result["skip_long_term_retrieval"] is False
        assert result["similar_memories"] == [{"memory_id": "m1"}]

    @pytest.mark.asyncio
    async def test_mem0_failure_degrades_to_empty(self):
        from mediZJ.lgraph.supervisor_graph import retrieve_memories_with_intent_gate

        coordinator = self._make_coordinator(intent="medical")
        coordinator.long_term_memory.search_similar_sessions = AsyncMock(
            side_effect=RuntimeError("mem0 down")
        )
        result = await retrieve_memories_with_intent_gate(
            coordinator,
            session_id="s3",
            question="我头痛",
            intent="medical",
        )

        assert result["skip_long_term_retrieval"] is False
        assert result["similar_memories"] == []


class TestChatModeRouting:
    """图级测试：others 意图 → chat_reply 直答，medical → 正常澄清/分解。"""

    def _make_coordinator(self, intent: str, chat_answer: str = "你好！有什么可以帮您？"):
        from unittest.mock import AsyncMock, MagicMock

        coordinator = type("Coordinator", (), {})()
        coordinator.short_term_memory = type("STM", (), {
            "get_recent_messages": AsyncMock(return_value=[]),
            "add_message": AsyncMock(return_value=None),
        })()
        coordinator.long_term_memory = type("LTM", (), {
            "search_similar_sessions": AsyncMock(return_value=[]),
        })()
        coordinator.personal_profile = type("PP", (), {"to_text": lambda self: "暂无"})()
        coordinator.questionnaire_manager = None  # 无问卷管理器，clarify 直接跳过
        coordinator._refresh_worker_profiles = lambda *args, **kwargs: None
        coordinator._save_long_term_memory = AsyncMock(return_value=None)

        # mock Worker（build_supervisor_graph 构造时会访问 get_worker）
        coordinator.get_worker = lambda agent_id: MagicMock()

        # mock LeadAgent：chat_reply 返回固定文本，assess_and_decompose 返回单任务
        coordinator.lead_agent = type("LA", (), {
            "chat_reply": AsyncMock(return_value={"answer": chat_answer}),
            "assess_and_decompose": AsyncMock(return_value={
                "subtasks": [{"description": "回答用户问题",
                              "assigned_agent": "consultation_agent"}],
            }),
            "clarify": AsyncMock(return_value={
                "clarified": False, "collected_info": "", "raw_answers": {},
            }),
            "set_on_thinking": lambda *a, **k: None,
            "set_on_thinking_done": lambda *a, **k: None,
        })()

        # 注入固定意图
        coordinator.intent_classifier = type("IC", (), {
            "classify": AsyncMock(return_value=IntentResult(
                intent=intent, confidence=0.9, source="llm", reason="test",
            )),
        })()
        return coordinator

    @pytest.mark.asyncio
    async def test_others_routes_to_chat_reply(self):
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph

        coordinator = self._make_coordinator(intent="others")
        graph = build_supervisor_graph(coordinator, tool_registry=None)
        result_state = await graph.ainvoke(
            {"question": "你好", "session_id": "c1"},
            config={"configurable": {"thread_id": "c1"}},
        )

        # 走了 chat_reply，没走任务分解
        coordinator.lead_agent.chat_reply.assert_awaited_once()
        coordinator.lead_agent.assess_and_decompose.assert_not_awaited()
        assert result_state["mode"] == "chat"
        assert result_state["final_answer"] == "你好！有什么可以帮您？"
        assert result_state["chat_mode"] is True
        assert result_state["agents_involved"] == ["lead_agent"]
        # 闲聊模式不保存 LTM
        coordinator._save_long_term_memory.assert_not_awaited()
        # 但会写入短期记忆（user + assistant），保证后续"我刚才问了什么"可召回
        stm_calls = coordinator.short_term_memory.add_message.await_args_list
        assert len(stm_calls) == 2
        assert stm_calls[0].kwargs["role"] == "user"
        assert stm_calls[0].kwargs["content"] == "你好"
        assert stm_calls[1].kwargs["role"] == "assistant"
        assert stm_calls[1].kwargs["content"] == "你好！有什么可以帮您？"

    @pytest.mark.asyncio
    async def test_medical_does_not_route_to_chat(self):
        """medical 意图：路由到正常流程（clarify），不是 chat_reply。"""
        from mediZJ.lgraph.supervisor_graph import route_by_intent

        # others 意图（chat_mode=True）→ chat_reply
        assert route_by_intent({"chat_mode": True, "intent": "others"}) == "chat_reply"
        # others 意图（仅 intent 字段）→ chat_reply
        assert route_by_intent({"intent": "others"}) == "chat_reply"
        # medical 意图 → 澄清决策
        assert route_by_intent({"intent": "medical", "chat_mode": False}) == "clarify_decide"
        # 缺省（无意图信息，如异常降级）→ 保守走正常流程
        assert route_by_intent({}) == "clarify_decide"


class TestEventType:
    """事件类型注册。"""

    def test_intent_classified_event_type(self):
        from mediZJ.swarm.events import EventType
        assert EventType.INTENT_CLASSIFIED.value == "intent_classified"
