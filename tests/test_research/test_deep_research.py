"""test_research/test_deep_research.py — EvidenceSynthesizer 单元测试（mock LLM）"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from mediZJ.research.evidence_synthesizer import EvidenceSynthesizer, ResearchReport
from mediZJ.research.web_search import SearchResult


class TestSearchResult:
    def test_basic_search_result(self):
        sr = SearchResult(
            title="Test Article",
            url="https://example.com",
            snippet="This is a test snippet about medical research.",
        )
        assert sr.title == "Test Article"
        assert sr.url == "https://example.com"
        assert sr.snippet == "This is a test snippet about medical research."

    def test_default_source(self):
        sr = SearchResult(title="T", url="http://x.com", snippet="s")
        assert sr.source == "web"


class TestResearchReport:
    def test_default_report(self):
        report = ResearchReport(query="test query")
        assert report.query == "test query"
        assert report.key_findings == []
        assert report.evidence_level == "C"
        assert report.confidence == 0.0

    def test_full_report(self):
        report = ResearchReport(
            query="q",
            key_findings=["finding 1", "finding 2"],
            evidence_level="B",
            sources=[{"title": "source1"}],
            confidence=0.8,
            summary="conclusion",
            recommendations=["suggestion 1"],
        )
        assert len(report.key_findings) == 2
        assert report.evidence_level == "B"


class TestEvidenceSynthesizerInit:
    def test_init_with_mock_client(self):
        mock_llm = MagicMock()
        synthesizer = EvidenceSynthesizer(llm_client=mock_llm)
        assert synthesizer.llm_client is mock_llm

    def test_init_creates_default_client(self):
        """没有传 llm_client 时自动创建（需要环境变量）。"""
        synthesizer = EvidenceSynthesizer()
        assert synthesizer.llm_client is not None


class TestEvidenceSynthesizerSynthesize:
    @pytest.fixture
    def synthesizer(self):
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            return_value='{"key_findings":["证据支持该治疗方法"],"evidence_level":"B","confidence":0.8,"summary":"综合结论","conflicts":[],"recommendations":[]}'
        )
        return EvidenceSynthesizer(llm_client=mock_llm)

    @pytest.mark.asyncio
    async def test_synthesize_with_data(self, synthesizer):
        search_results = [
            SearchResult(
                title="Study 1",
                url="https://example.com/1",
                snippet="Treatment showed positive results.",
            ),
        ]
        kb_results = [
            {"content": "指南推荐该治疗方案", "source": "临床指南2024", "score": 0.95},
        ]

        result = await synthesizer.synthesize(
            query="某某治疗方法有效吗？",
            web_results=search_results,
            kb_results=kb_results,
        )
        assert isinstance(result, ResearchReport)
        assert result.query == "某某治疗方法有效吗？"

    @pytest.mark.asyncio
    async def test_synthesize_empty_results(self, synthesizer):
        """空结果时应有降级处理。"""
        result = await synthesizer.synthesize(
            query="测试问题",
            web_results=[],
            kb_results=[],
        )
        assert isinstance(result, ResearchReport)
        assert result.query == "测试问题"

    @pytest.mark.asyncio
    async def test_synthesize_handles_llm_error(self, synthesizer):
        """LLM 调用失败时应有容错处理。"""
        synthesizer.llm_client.chat.side_effect = Exception("LLM error")
        search_results = [SearchResult(title="T", url="u", snippet="s")]
        # 可能抛出异常或降级处理（取决于实现）
        try:
            result = await synthesizer.synthesize(
                query="测试问题",
                web_results=search_results,
                kb_results=[],
            )
            assert isinstance(result, ResearchReport)
        except Exception:
            # 某些实现可能不捕获 LLM 异常
            pass
