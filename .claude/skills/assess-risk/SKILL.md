---
name: assess-risk
description: Assess symptom risk level (low/medium/high/emergency). Use when user describes symptoms and needs risk evaluation to determine urgency of medical attention.
---

# Assess Risk (风险评估)

评估症状的风险等级，判断是否需要紧急就医。

## When to Use

- 用户描述症状，需要评估严重程度
- 判断是否需要紧急就医
- 风险分级（低/中/高/紧急）

## 底层实现

- 技术: 风险规则引擎 + Milvus 向量数据库
- 数据源: 高风险症状规则库 + 医学知识库（RAG）
- 增强: 从知识库检索风险相关的医学建议

## 调用方式

```bash
/assess-risk 胸痛,呼吸困难
```
