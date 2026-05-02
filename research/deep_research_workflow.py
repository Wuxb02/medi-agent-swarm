"""
深度研究工作流

编排多步骤研究流程：查询规划 → 搜索 → 检索 → 综合 → 验证
"""
from typing import List, Dict, Any, Optional
from loguru import logger
import asyncio

from core import LLMClient
from research.web_search import WebSearchTool, SearchResult
from knowledge.milvus_kb import MedicalKnowledgeBase
from research.evidence_synthesizer import EvidenceSynthesizer, ResearchReport

# 全局知识库实例（单例）
_kb_instance = None

def get_knowledge_base():
    """获取知识库单例"""
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = MedicalKnowledgeBase()
    return _kb_instance


class DeepResearchWorkflow:
    """
    深度研究工作流

    功能：
    - 多步骤研究流程编排
    - 查询规划和优化
    - 并行搜索和检索
    - 证据综合和质量控制
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        use_web_search: bool = True,
        use_knowledge_base: bool = True
    ):
        """
        初始化工作流

        Args:
            llm_client: LLM 客户端
            use_web_search: 是否使用网络搜索
            use_knowledge_base: 是否使用 Milvus 知识库
        """
        self.llm_client = llm_client or LLMClient()
        self.use_web_search = use_web_search
        self.use_knowledge_base = use_knowledge_base

        # 初始化组件
        self.web_search = WebSearchTool() if use_web_search else None
        # 使用 Milvus 知识库单例（和其他 Skills 共享，避免重复加载模型）
        self.knowledge_base = get_knowledge_base() if use_knowledge_base else None
        self.synthesizer = EvidenceSynthesizer(llm_client=self.llm_client)

    async def run(
        self,
        question: str,
        max_web_results: int = 10,
        max_kb_results: int = 5
    ) -> ResearchReport:
        """
        执行深度研究

        Args:
            question: 研究问题
            max_web_results: 最大网络搜索结果数
            max_kb_results: 最大知识库检索结果数

        Returns:
            研究报告
        """
        logger.info(f"Starting DeepResearch for: {question}")

        # Step 1: 查询规划
        sub_queries = await self._plan_queries(question)
        logger.info(f"Planned {len(sub_queries)} sub-queries")

        # Step 2: 并行搜索
        web_results: List[SearchResult] = []
        kb_results: List[Document] = []

        search_tasks = []

        if self.use_web_search and self.web_search:
            # 网络搜索
            for query in sub_queries[:3]:  # 限制子查询数量
                search_tasks.append(
                    self.web_search.search(query, max_results=max_web_results // len(sub_queries))
                )

        if self.use_knowledge_base and self.knowledge_base:
            # 从 Milvus 知识库检索
            for query in sub_queries[:3]:
                search_tasks.append(
                    self._search_milvus(query, top_k=max_kb_results // len(sub_queries))
                )

        # 并行执行
        if search_tasks:
            results = await asyncio.gather(*search_tasks, return_exceptions=True)

            # 分离结果
            for result in results:
                if isinstance(result, Exception):
                    logger.warning(f"Search task failed: {result}")
                    continue

                if isinstance(result, list):
                    if len(result) > 0:
                        if isinstance(result[0], SearchResult):
                            web_results.extend(result)
                        elif isinstance(result[0], dict):
                            # Milvus 返回的是字典列表
                            kb_results.extend(result)

        logger.info(f"Collected {len(web_results)} web results, {len(kb_results)} KB results")

        # Step 3: 证据综合
        report = await self.synthesizer.synthesize(
            query=question,
            web_results=web_results,
            kb_results=kb_results
        )
        if not report.key_findings:
            logger.warning("Report has no key findings")

        if not report.summary:
            logger.warning("Report has no summary")

        logger.info("DeepResearch completed")
        return report

    async def _search_milvus(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        从 Milvus 知识库搜索

        Args:
            query: 查询文本
            top_k: 返回结果数量

        Returns:
            文档列表（字典格式）
        """
        try:
            results = self.knowledge_base.search(query=query, top_k=top_k, filter_type=None)
            logger.debug(f"Milvus search returned {len(results)} results for: {query[:50]}...")
            return results
        except Exception as e:
            logger.error(f"Milvus search failed: {e}")
            return []

    async def _plan_queries(self, question: str) -> List[str]:
        """
        查询规划：将复杂问题拆解为多个子查询

        Args:
            question: 原始问题

        Returns:
            子查询列表
        """
        prompt = f"""你是医学研究助手。请将以下问题拆解为 2-3 个更具体的子查询，以便进行深度研究。

原始问题：{question}

要求：
1. 每个子查询应该聚焦一个特定方面
2. 子查询应该互补，覆盖问题的不同角度
3. 子查询应该简洁明确

输出格式：
每行一个子查询，不需要编号。

示例：
原始问题：2型糖尿病如何治疗？
子查询1：2型糖尿病的药物治疗方案
子查询2：2型糖尿病的生活方式管理
子查询3：2型糖尿病的并发症预防
"""

        try:
            response = await self.llm_client.chat([
                {"role": "user", "content": prompt}
            ])

            # 解析子查询
            lines = response.strip().split('\n')
            sub_queries = []

            for line in lines:
                line = line.strip()
                # 移除可能的编号
                line = line.lstrip('0123456789.-:：）) ')
                if line and len(line) > 5:  # 过滤太短的行
                    sub_queries.append(line)

            # 至少包含原始问题
            if not sub_queries:
                sub_queries = [question]

            # 限制数量
            sub_queries = sub_queries[:3]

            return sub_queries

        except Exception as e:
            logger.error(f"Query planning error: {e}")
            # 降级：返回原始问题
            return [question]

    async def research_with_refinement(
        self,
        question: str,
        max_iterations: int = 2
    ) -> ResearchReport:
        """
        带细化的研究（多轮迭代）

        Args:
            question: 研究问题
            max_iterations: 最大迭代次数

        Returns:
            最终研究报告
        """
        logger.info(f"Starting iterative research (max_iterations={max_iterations})")

        report = None

        for iteration in range(max_iterations):
            logger.info(f"Iteration {iteration + 1}/{max_iterations}")

            # 执行研究
            report = await self.run(question)

            # 检查质量
            if report.confidence >= 0.7 and len(report.key_findings) >= 3:
                logger.info(f"High-quality report achieved in iteration {iteration + 1}")
                break

            # 如果是最后一轮，直接返回
            if iteration == max_iterations - 1:
                break

            # 细化查询（基于当前结果）
            if report.key_findings:
                question = f"{question}（关注：{report.key_findings[0]}）"

        return report


# 便捷函数
async def deep_research(
    question: str,
    use_web: bool = True,
    use_kb: bool = True
) -> ResearchReport:
    """
    快速执行深度研究

    Args:
        question: 研究问题
        use_web: 是否使用网络搜索
        use_kb: 是否使用知识库

    Returns:
        研究报告
    """
    workflow = DeepResearchWorkflow(
        use_web_search=use_web,
        use_knowledge_base=use_kb
    )
    return await workflow.run(question)
