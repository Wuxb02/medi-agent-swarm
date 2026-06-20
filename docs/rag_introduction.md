# 知识库 RAG 检索系统

基于 Milvus 向量数据库，构建三路混合检索引擎（Dense + BM25 + Entity Boost），配合全链路引用标注，为医疗问答提供高精度检索支撑。

## 一、检索架构总览

```text
用户查询
  │
  ├─ Path 1: Dense 语义检索  ──┐
  ├─ Path 2: BM25 稀疏检索   ──┤ Milvus RRF 融合 (k=60)
  └─ Path 3: Entity 实体加权 ──┤ 应用层叠加 (+0.15)
                                │
                                ▼
                         按 doc_id 去重 → 最终 Top-K
```

三路各自捕捉不同维度的相关性，最后统一融合取最高分，对外 `search()` 签名保持不变，所有 Skill 无需改动。

## 二、Path 1 — Dense 语义检索

- **嵌入模型**：`BAAI/bge-small-zh-v1.5`（512 维）
- **检索方式**：`IP`（内积）近似最近邻搜索，`nprobe=16`
- **特点**：捕捉语义级别的相似性，对同义表达、改写问法有较好的泛化能力

## 三、Path 2 — BM25 稀疏检索

- **实现**：Milvus 内置 `BM25` Function，在写入时对文本自动生成 sparse vector，查询时通过 `SPARSE_INVERTED_INDEX` 做稀疏匹配
- **特点**：精确关键词命中，对术语、名词短语等词面匹配场景更敏感，弥补 Dense 对罕见医学术语召回不足的问题

## 四、Path 1+2 融合 — Milvus RRF

两路各自取 `top_k × 3` 条候选，由 Milvus `hybrid_search` 接口使用 `RRFRanker(k=60)` 进行倒数排序融合：

```
RRF(doc) = Σ 1 / (k + rank_i(doc))   # k = 60
```

`k=60` 是平滑参数，防止某一路排名极高时直接淹没另一路结果。该步骤在 Milvus 内部完成。

## 五、Path 3 — 医学实体精确加权（Entity Boost）

### 5.1 构建阶段

系统启动时，从知识库全量文档通过 **jieba 分词 + 规则过滤** 自抽取医学实体，构建内存倒排索引。

**实体识别规则**（满足其一即视为医学实体）：

| 条件 | 规则 | 示例 |
|---|---|---|
| ICD 编码 | `^[A-Z]\d{2}(\.\d+)?$` | `I10`（高血压）、`E11.2`（2型糖尿病肾病） |
| 英文缩写 | `^[A-Z]{2,8}$` | `ACEI`、`CCB`、`MRI`、`CT` |
| 药品名后缀 | 正则匹配 `ine\|ol\|ide\|...` 后缀 | `metformin`、`lisinopril` |
| 中文医学名词 | 全中文字符，长度 2-12 | `高血压`、`冠状动脉粥样硬化` |

同时过滤掉 `_STOP_WORDS` 中的通用词（`患者`、`治疗`、`药物`、`症状` 等）。

**索引结构**：

```text
"高血压"     → {doc_001, doc_005, doc_012}
"二甲双胍"   → {doc_005, doc_023}
"I10"        → {doc_001}
...
```

纯内存 `dict[str, set[str]]`，O(1) 查表，支持文档增删时的增量同步。

### 5.2 查询阶段

对用户 query 做同样的 jieba 分词 + 规则过滤，提取医学实体后查倒排表：

```python
# 伪代码
query_entities = extract(user_query)  # 如 ["高血压", "二甲双胍"]

doc_scores = {}  # doc_id → 命中实体数
for entity in query_entities:
    for doc_id in index[entity]:
        doc_scores[doc_id] += 1.0

# 归一化到 [0, 1]
return {doc_id: count/max_score for ...}
```

得分越高说明该文档与 query 共享的医学实体越多。

### 5.3 融合叠加

Milvus RRF 结果返回后，在应用层叠加 Entity Boost：

```python
ENTITY_BONUS_COEFFICIENT = 0.15

normalized_rrf = raw_rrf / MAX_RRF_SCORE     # RRF 分数归一化
bonus = entity_boost.get(doc_id, 0.0) * 0.15  # 实体加权
final_score = min(normalized_rrf + bonus, 1.0)  # 上限 1.0
```

**设计意图**：Milvus RRF 依赖 embedding / BM25 做语义匹配，对精确术语不敏感（如"心肌梗死"和"心梗"语义近但词面不同）。Entity Boost 作为确定性补充，当 query 中出现与知识库文档精确匹配的医学实体时给予固定加分，保证术语级检索准确率。

最终按 `doc_id` 去重，保留 `final_score` 最高的记录。

## 六、全链路引用标注

RAG 检索结果以结构化 `references` 数组返回，每个引用包含以下字段：

```json
{
  "index": 1,
  "doc_id": "doc_001",
  "source": "临床指南",
  "disease": "高血压",
  "type": "guideline",
  "filename": "hypertension_2024.md",
  "score": 0.87,
  "snippet": "...",
  "content": "..."
}
```

### 后端链路

- **Skill 层**：`search-knowledge`、`clinical-guideline`、`deep-research` 三个 RAG Skill 统一返回结构化 `references` 数组
- **AgentLoop**：工具执行后自动收集 references，按 `doc_id` 去重，附入 Worker 最终 result
- **SwarmCoordinator**：
  - 单 Agent 场景：直接透传
  - Swarm 场景：跨 Worker 收集 → 去重 → 全局重编号 → 替换贡献文本中的旧编号
- **输出**：SSE `done` 事件 / JSON 事件文件 / non-stream `ChatResponse` 三路径携带 `citations`

LeadAgent 基于检索结果生成回答时，句尾自动附加 `[N]`、`[N,M]` 可点击引用标记。

### 前端渲染

- `useMarkdown.ts`：渲染后正则匹配 `[N]`、`[N,M]`、`[N-M]` 替换为 `<sup class="citation-ref">` 上标元素
- `CitationPopover.vue`：Teleport 浮层，scroll/resize 实时跟随引用位置，外部点击关闭，固定高度滚动区展示 chunk 全文 + 来源 / 疾病 / 类型 / 相关度
- `ChatMessage.vue`：集成点击事件驱动浮层弹出

### 持久化

`messages` 表新增 `citations` 列（自动迁移），`save_turn` 写入 / `get_session` 反序列化。历史会话加载后引用标注仍可点击交互。
