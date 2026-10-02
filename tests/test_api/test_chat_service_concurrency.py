"""最终回答引用与事件持久化格式测试"""

from types import SimpleNamespace


import mediZJ.api.services.chat_service as cs


def test_reference_section_only_contains_cited_items():
    """原问答形态：检索十项，正文只引用第一和第四项。"""
    citations = [
        {"index": index, "filename": f"资料{index}.txt"} for index in range(1, 11)
    ]
    answer = "头痛应就医[1]，并观察危险信号[1,4]。\n\n## 参考资料\n\n" + "\n\n".join(
        f"[{index}] 资料{index}.txt" for index in range(1, 11)
    )

    final_answer, final_citations = cs._keep_cited_references(answer, citations)

    assert [item["index"] for item in final_citations] == [1, 4]
    assert final_answer.count("## 参考资料") == 1
    assert "[2] 资料2.txt" not in final_answer
    assert "[4] 资料4.txt" in final_answer


def test_reference_filter_handles_ranges_and_no_citation():
    citations = [
        {"index": index, "filename": f"资料{index}.txt"} for index in range(1, 5)
    ]
    answer, kept = cs._keep_cited_references("观察[2-3]。", citations)
    assert [item["index"] for item in kept] == [2, 3]
    assert "[1] 资料1.txt" not in answer

    answer, kept = cs._keep_cited_references(
        "请就医。\n\n## 参考资料\n\n[1] 资料1.txt", citations
    )
    assert answer == "请就医。"
    assert kept == []


async def test_final_gate_keeps_answer_and_api_citations_in_sync(monkeypatch):
    citations = [
        {"index": 1, "filename": "已引用.txt"},
        {"index": 2, "filename": "未引用.txt"},
    ]

    class FakeVerifier:
        async def verify_and_rewrite(self, _question, _answer, _citations):
            verification = SimpleNamespace(validated_citations=citations)
            verification.to_dict = lambda: {
                "validated_citations": verification.validated_citations
            }
            return "建议就医[1]。\n\n## 参考资料\n\n[2] 未引用.txt", verification

    monkeypatch.setattr(cs, "_get_answer_verifier", lambda: FakeVerifier())
    result = await cs._verify_final_result(
        "头痛",
        {
            "answer": "待校验回答",
            "citations": citations,
        },
    )
    assert [item["index"] for item in result["citations"]] == [1]
    assert result["verification"]["validated_citations"] == result["citations"]
    assert "未引用.txt" not in result["answer"]


def test_merge_thinking_events_preserves_phase_and_envelope():
    """历史持久化合并后应与实时 SSE 保持相同的事件结构。"""
    events = [
        {
            "event": "agent_thinking",
            "data": {
                "source_agent": "lead_agent",
                "timestamp": "2026-08-13T01:00:00",
                "data": {
                    "content": "正在综合",
                    "iteration": 2,
                    "phase": "synthesize",
                    "title": "结果汇总",
                    "status": "running",
                },
            },
        },
        {
            "event": "agent_thinking",
            "data": {
                "source_agent": "lead_agent",
                "timestamp": "2026-08-13T01:00:01",
                "data": {
                    "content": " Worker 结果",
                    "iteration": 2,
                    "phase": "synthesize",
                    "title": "结果汇总",
                    "status": "completed",
                },
            },
        },
    ]

    merged = cs._merge_thinking_events(events)

    assert len(merged) == 1
    assert merged[0]["data"]["source_agent"] == "lead_agent"
    assert merged[0]["data"]["timestamp"] == "2026-08-13T01:00:00"
    assert merged[0]["data"]["data"] == {
        "content": "正在综合 Worker 结果",
        "iteration": 2,
        "phase": "synthesize",
        "title": "结果汇总",
        "status": "completed",
    }
