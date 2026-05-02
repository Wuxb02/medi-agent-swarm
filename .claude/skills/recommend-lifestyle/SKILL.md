---
name: recommend-lifestyle
description: Provide lifestyle and medication guidance based on disease or symptoms. Use when user asks about diet, exercise, sleep advice, or basic medication guidance for specific conditions.
---

# Recommend Lifestyle (生活方式建议)

根据疾病或症状提供生活方式建议，包括饮食、运动、睡眠和基础用药指导。

## When to Use

- 用户问"高血压患者饮食注意什么""糖尿病如何运动"
- 需要生活方式调整建议
- 需要基础用药指导

## 底层实现

- 数据源: Milvus 向量数据库

## 调用方式

```bash
/recommend-lifestyle 高血压
```
