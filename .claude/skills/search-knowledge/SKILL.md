---
name: search-knowledge
description: Search medical knowledge base. Use when user asks about medical information, disease details, or general health knowledge. Fast semantic search powered by Milvus vector database.
---

# Search Medical Knowledge (搜索医学知识库)

快速搜索医学知识库，获取疾病、症状、治疗等相关信息。

## When to Use

- 用户问"高血压是什么""糖尿病的症状有哪些"
- 需要查询通用医学知识
- 简单、单步查询（不需要多步推理）

## 底层实现

- 技术: Milvus 向量数据库 + 语义检索
- 速度: 快速（<1秒）

## 调用方式

```bash
/search-knowledge 高血压的治疗方法
```

## 返回格式

```json
{
  "answer": "格式化的知识库检索结果",
  "total_found": 3,
  "query": "高血压的治疗方法"
}
```
