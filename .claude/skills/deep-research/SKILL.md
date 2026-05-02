---
name: deep-research
description: Conduct deep research combining web search, knowledge base, and evidence synthesis. Use for complex medical questions requiring latest research or comprehensive literature review.
---

# Deep Research (深度研究)

综合网络搜索、知识库和证据综合的深度研究能力。

## When to Use

- 复杂的医学问题，需要多源信息综合
- 需要最新研究进展和文献综述
- 需要高置信度的证据支持

## 底层实现

- 工作流: `DeepResearchWorkflow`
- 数据源: Web Search + Milvus 向量数据库 + 证据综合
- 技术: 并行搜索和检索 + LLM 证据综合

## 调用方式

```bash
/deep-research 糖尿病的最新治疗方法
```
