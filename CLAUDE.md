# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

MediZJ Agent Swarm — 基于 Skill + Tool 双层架构的多智能体医疗助手系统。采用去中心化 Swarm 模式协调多个 AI Agent，提供医疗咨询、症状诊断和研究支持。

## 常用命令

```bash
# 环境安装
conda create -n medix-swarm python=3.12 -y && conda activate medix-swarm
pip install -r requirements.txt
# 或使用 uv:
uv sync

# 配置环境变量
cp .env.example .env  # 编辑填入 LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME 等

# 初始化知识库（Milvus 向量数据库）
python knowledge/scripts/import_hardcoded_data.py
python knowledge/scripts/deduplicate.py  # 数据去重

# 初始化会话数据库（SQLite + Milvus）
python memory/scripts/init_session_db.py          # 首次初始化
python memory/scripts/init_session_db.py --clean  # 清除后重新初始化

# 运行应用
python main.py          # 交互模式
python main.py -v       # 详细日志模式

# Web 模式（两个终端）
uv run python api_main.py                      # 后端 API，默认 8000 端口
cd frontend && npm install && npm run dev      # 前端，http://localhost:5173

# 运行测试（231 个单元测试 + 20 个集成测试）
# 单元测试默认执行；集成测试因依赖真实 LLM/Milvus/Mem0 默认跳过，
# 传 --run-integration 启用。集成测试依赖 .env 中的 LLM_API_KEY / LLM_BASE_URL 配置。
pytest test/ -m "not integration"                     # 仅单元测试（快速，无需外部服务）
pytest test/ -m "integration" --run-integration       # 仅集成测试（需要 .env 配置）
pytest test/ --run-integration                        # 全部测试
pytest test/ -m "not integration" --cov --cov-report=html  # 覆盖率报告

# 运行评估（5 项指标）
python -m eval.runner --metrics all
python -m eval.runner --metrics routing,retrieval

# 前端构建
cd frontend && npm run build
```

## 架构设计

### 请求处理流程

```text
用户输入 → main.py/api_main.py → SwarmCoordinator
  │
  ├─ 检索长短期记忆，构建增强上下文
  │
  ├─ LeadAgent.clarify()  ← 信息澄清：通过结构化问卷收集背景信息
  │
  ├─ LeadAgent.assess_and_decompose()  ← 判断复杂度并分解
  │
  ├─ 1 个子任务 → Agent 通过 process_subtask() 执行（隔离子会话）→ 最终回答
  │
  └─ ≥2 个子任务 → Swarm 模式
       ├─ LeadAgent.create_subtasks()   ← 创建 SubTask 写入 SharedContext
       ├─ Worker 通过 process_subtask() 自主认领并行执行
       │    ├─ ConsultationAgent (健康咨询)
       │    ├─ DiagnosticAgent (症状诊断)
       │    └─ ResearchAgent (医学研究)
       └─ LeadAgent.synthesize_results() ← 汇总结果 → 最终回答
```

每个 Worker Agent 内部运行 `AgentLoop`（Think-Act-Observe 循环），每次最多执行 2 次工具调用。所有 Worker 使用独立子会话 ID（`{session_id}:{agent_id}:{subtask_id}`），无历史上下文。

### 核心模块

| 模块 | 职责 |
| --- | --- |
| `core/llm_client.py` | OpenAI 兼容的异步 LLM 客户端，支持流式、function calling |
| `core/agent_loop.py` | 核心执行引擎：Think-Act-Observe 循环，集成约束验证、自动修复、动态工具刷新、问卷暂停/恢复 |
| `core/skill_registry.py` | 双层注册：Skill（能力包）+ Tool（底层函数），compat_mode 自动检测 |
| `core/skill_loader.py` | 从 `.claude/skills/` 动态发现技能，提取 SKILL.md 正文作为指令 |
| `core/skill_models.py` | `SkillDefinition` 数据模型 |
| `core/prompt_loader.py` | Jinja2 模板加载器，从 `prompt/` 目录加载模板 |
| `core/questionnaire_manager.py` | asyncio.Future 问卷暂停/恢复管理器 |
| `core/tools/activate_skill.py` | `activate_skill` 工具工厂 |
| `core/tools/questionnaire.py` | `question_for_user` 工具（XML 问卷解析） |
| `agents/base_agent.py` | Agent 抽象基类，集成 SkillRegistry + AgentLoop |
| `agents/skill_registry_mixin.py` | Worker Agent 共享的技能自动注册 Mixin |
| `swarm/swarm_coordinator.py` | 顶层协调器：记忆检索 + 路由分发 + 并行调度（90s 超时） |
| `swarm/lead_agent.py` | 信息澄清 + 复杂度评估 + 任务分解 + 结果综合 |
| `swarm/shared_context.py` | 共享黑板系统（SubTask/Contribution 生命周期管理） |
| `swarm/events.py` | 事件驱动通信（13 种事件类型，含 AGENT_QUESTIONNAIRE） |
| `memory/short_term.py` | 短期记忆（单例，写时增量压缩，支持内存/Redis） |
| `memory/long_term.py` | 长期记忆（Mem0 云服务，经 LLM 质量门控过滤） |
| `memory/entropy_manager.py` | 熵管理器：向量语义去重 + LLM 摘要 + 截断降级 |
| `memory/session_db.py` | SQLite 会话数据库（sessions + messages 表） |
| `memory/session_vector_store.py` | Milvus 会话向量索引（session_summaries 集合） |
| `memory/personal_profile.py` | 个人健康档案（`memory/profile/PERSONAL.md`） |
| `memory/embedding.py` | 共享 embedding 工具（BAAI/bge-small-zh-v1.5，512 维） |
| `knowledge/milvus_kb.py` | Milvus Lite 向量知识库（单例）— 三路混合检索：Dense + BM25 + Entity Boost |
| `knowledge/entity_index.py` | 轻量级医学实体倒排索引：jieba 自抽取 + 内存映射，支持查询时精确命中加权 |
| `research/deep_research_workflow.py` | 多步骤研究流水线 |
| `constraints/validator.py` | 运行时约束验证（工具权限、输出质量） |
| `validation/auto_fixer.py` | 自动修复违规输出（添加免责声明、警告等） |

### 关键设计模式

- **单例模式**：`MedicalKnowledgeBase`、`ShortTermMemory`、`SessionDB`、`SessionVectorStore`（`__new__` 实现）
- **Mixin 模式**：`SkillRegistryMixin` 为所有 Worker Agent 提供共享的技能注册
- **共享黑板**：`SharedContext` 作为去中心化通信介质，Worker 自主认领任务
- **Harness Engineering**：非侵入式约束验证 + 自动修复注入 AgentLoop
- **事件驱动**：`Event` 系统 + `on_event_callback` 用于 SSE 流式推送

### Skill + Tool 双层架构

位于 `.claude/skills/`，每个 Skill 包含 `SKILL.md`（YAML frontmatter + Markdown 正文）和 `script/`（Python 实现）。

调用流程：LLM 看到所有 Skill 描述 → `activate_skill("name")` → 指令注入 system prompt + 工具动态加载 → 执行任务 → 激活新 Skill 自动停用前一个。

10 个医疗 Skills：`search-knowledge`、`assess-risk`、`analyze-symptoms`、`recommend-lifestyle`、`disease-code`、`clinical-guideline`、`deep-research`、`search-history`、`search-similar-cases`、`render-markdown-html`

### 知识库 — 三路混合检索

`MedicalKnowledgeBase.search()` 内部采用 **Dense + BM25 + Entity Boost** 三路融合，对外签名不变，所有 Skill 无需改动。

| 路径 | 方法 | 说明 |
| --- | --- | --- |
| Path 1 — Dense | bge-small-zh-v1.5 → IP ANN | 稠密语义向量检索 |
| Path 2 — BM25 | Milvus 内置 BM25 Function → SPARSE_INVERTED_INDEX | 稀疏关键词检索 |
| Path 3 — Entity | jieba 分词 + 内存倒排索引 | 医学实体精确命中加权 |

融合：Milvus RRF (Path 1+2, k=60) + App-level Entity Boost (+0.15)，最终 `min(RRF_norm + entity_bonus × 0.15, 1.0)`。按 `doc_id` 去重保留最高分。

`MedicalEntityIndex`（`knowledge/entity_index.py`）：启动时从 Milvus 全量文档自抽取实体（中文字符 2-12 字、ICD 编码、药品后缀），构建 `entity → Set[doc_id]` 内存倒排；文档增删时增量同步。

### 知识库引用标注

LeadAgent 基于 RAG 结果生成回答时，检索 chunk 的句尾自动附加 `[N]` `[N,M]` 可点击引用标注。

**后端链路**：
- 三个 RAG Skill（`search-knowledge` / `clinical-guideline` / `deep-research`）统一返回结构化 `references` 数组：`[{index, doc_id, source, disease, type, filename, score, snippet, content}]`
- `AgentLoop` 工具执行后自动收集 references，按 `doc_id` 去重，附入 Worker 最终 result
- `SwarmCoordinator`：单 Agent 直接透传；Swarm 模式跨 Worker 收集 → 去重 → 重编号 → 替换贡献文本旧编号
- `synthesis.j2` 指示 LeadAgent 保留引用编号
- SS done / JSON 事件文件 / non-stream ChatResponse 三路径携带 `citations`

**前端渲染**：
- `useMarkdown.ts`：渲染后正则匹配 `[N]` `[N,M]` `[N-M]` 替换为 `<sup class="citation-ref">`
- `CitationPopover.vue`：Teleport 浮层，scroll/resize 实时跟随引用位置，外部点击关闭，固定高度滚动区展示 chunk 全文 + 来源/疾病/类型/相关度
- `ChatMessage.vue`：集成点击事件驱动 Popover

**持久化**：`messages` 表新增 `citations TEXT` 列（自动迁移），`save_turn` 写入 / `get_session` 反序列化；历史会话加载后引用标注仍可点击。

### Prompt 管理

所有 prompt 集中在 `prompt/` 目录，基于 Jinja2 模板引擎，18 个 `.j2` 模板分 6 个子目录：

```python
from core.prompt_loader import PromptLoader
system_prompt = PromptLoader.load("agents/consultation_system.j2")
user_msg = PromptLoader.render("swarm/assessment_user.j2", question="...", recent_history=[...])
```

### 会话持久化（SQLite + Milvus 双引擎）

每轮对话完成后自动持久化，三层回退加载（SQLite → .json → .md）。

- **SQLite**（`memory/data/sessions.db`）：结构化消息存储，事务原子写入
- **Milvus**（`memory/data/session_vectors.db`）：会话摘要向量索引，语义搜索
- **初始化**：`python memory/scripts/init_session_db.py`

### 记忆系统（三层）

| 层级 | 存储 | 用途 |
| --- | --- | --- |
| 短期记忆 | 内存（默认）/Redis | 会话级对话历史，写时增量压缩，仅供 LeadAgent 参考 |
| 个人档案 | `memory/profile/PERSONAL.md`（本地文件） | 患者信息（年龄/性别/病史/过敏史），AgentLoop 注入为 system message |
| 长期记忆 | Mem0 云服务 | 跨会话可复用医学事实，经 LLM 质量门控（score < 5 跳过） |

未设置 `MEM0_API_KEY` 时优雅降级，仅使用短期记忆和个人档案。

## 配置

环境变量（`.env`）：
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_NAME` — OpenAI 兼容 LLM 配置
- `LLM_TEMPERATURE`（默认 0.7）、`LLM_MAX_TOKENS`（默认 8192）
- `EMBEDDING_MODEL_NAME`（默认 `BAAI/bge-small-zh-v1.5`）
- `MEM0_API_KEY` — 可选，Mem0 长期记忆服务

约束定义（YAML）：
- `constraints/agent_constraints.yaml` — 各 Agent 能力边界、允许工具、禁止行为
- `constraints/swarm_constraints.yaml` — Swarm 协作规则、任务分解策略

## 已知问题

- 无 linting/formatting/CI 配置
