---
name: analyze-symptoms
description: Analyze symptom patterns and potential disease associations. Use when user describes multiple symptoms and needs pattern analysis or differential diagnosis suggestions.
---

# Analyze Symptoms (症状分析)

分析症状模式和潜在疾病关联，用于鉴别诊断。

## When to Use

- 用户描述多个症状，需要模式分析
- 需要鉴别诊断建议
- 评估症状所涉及的身体系统

## 底层实现

- 技术: 症状分类规则引擎 + Milvus 向量数据库
- 数据源: 本地症状规则库 + 医学知识库（RAG）
- 增强: 从知识库检索疾病详细信息

## 调用方式

```bash
/analyze-symptoms 头痛,发热,咳嗽
```
