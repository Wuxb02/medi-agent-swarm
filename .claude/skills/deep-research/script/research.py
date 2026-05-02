"""
Deep Research Skill
深度研究 Skill（依赖 RAG 知识库 + Web Search）
整合网络搜索和 Milvus 医学知识库进行深度研究
"""
from typing import Dict, Any
from loguru import logger


async def deep_research(query: str, max_iterations: int = 2) -> Dict[str, Any]:
    """
    深度研究

    Args:
        query: 研究问题
        max_iterations: 最大迭代次数（默认2）

    Returns:
        {
            "answer": "格式化的研究报告",
            "findings": ["发现1", "发现2"],
            "confidence": "high/medium/low"
        }
    """
    logger.info(f"Starting deep research: query={query}, max_iterations={max_iterations}")

    # 调用深度研究工作流
    import sys
    from pathlib import Path
    # 确保项目根目录在 sys.path 中
    project_root = Path(__file__).parent.parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from research.deep_research_workflow import DeepResearchWorkflow

    workflow = DeepResearchWorkflow()

    try:
        # 执行研究
        # 注意：DeepResearchWorkflow.run() 返回的是 ResearchReport 对象，不是 dict
        report = await workflow.run(
            question=query,  # 参数名是 question，不是 query
            max_web_results=max_iterations * 5,  # 使用 max_iterations 控制搜索结果数
            max_kb_results=max_iterations * 3
        )

        # report 是 ResearchReport 对象，有以下属性：
        # - key_findings: List[str]
        # - evidence_level: str
        # - confidence: float
        # - sources: List[Dict]
        # - summary: str
        # - recommendations: List[str]

        return {
            "answer": format_research_report(query, report),
            "findings": report.key_findings,
            "confidence": "high" if report.confidence > 0.7 else "medium" if report.confidence > 0.4 else "low",
            "sources": len(report.sources),
            "evidence_level": report.evidence_level,
            "status": "completed",
            "data_sources": "Web Search + Milvus RAG + Evidence Synthesis"
        }

    except Exception as e:
        logger.error(f"Deep research failed: {e}")
        return {
            "answer": f"深度研究失败：{str(e)}",
            "findings": [],
            "confidence": "low",
            "sources": 0,
            "status": "error"
        }


def format_research_report(query: str, report) -> str:
    """
    格式化研究报告

    Args:
        query: 原始查询
        report: ResearchReport 对象（来自 evidence_synthesizer.py）
    """
    output = [
        "【深度研究报告】\n",
        f"研究问题：{query}\n"
    ]

    # 关键发现
    if report.key_findings:
        output.append("关键发现：")
        for i, finding in enumerate(report.key_findings, 1):
            output.append(f"{i}. {finding}")
        output.append("")

    # 综合总结
    if report.summary:
        output.append(f"综合分析：\n{report.summary}\n")

    # 证据等级
    output.append(f"证据等级：{report.evidence_level} 级")

    # 置信度
    confidence_percent = f"{report.confidence:.0%}"
    output.append(f"置信度：{confidence_percent}")

    # 信息冲突（如果有）
    if report.conflicts:
        output.append("\n信息冲突：")
        for conflict in report.conflicts:
            output.append(f"- {conflict}")

    # 建议（如果有）
    if report.recommendations:
        output.append("\n建议：")
        for i, rec in enumerate(report.recommendations, 1):
            output.append(f"{i}. {rec}")

    # 来源数量
    if report.sources:
        output.append(f"\n参考来源数量：{len(report.sources)}")

    output.append("\n💡 数据来源：网络搜索 + 医学知识库（Milvus RAG）+ 证据综合")

    return "\n".join(output)


def deep_research_sync(query: str, max_iterations: int = 2) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(deep_research(query, max_iterations))
