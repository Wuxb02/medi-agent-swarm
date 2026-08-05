# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

MediZJ Agent Swarm — 基于 Skill + Tool 双层架构的多智能体医疗助手系统。采用去中心化 Swarm 模式协调多个 AI Agent，提供医疗咨询、症状诊断和研究支持。

## 常用命令

```bash
# 环境安装
conda create -n medix-swarm python=3.12 -y && conda activate medix-swarm
# 依赖安装（推荐 uv，lockfile 锁定）：
uv sync
# 或 pip（依赖定义在 pyproject.toml，仓库不维护 requirements.txt）：
pip install -e .

# 配置环境变量
cp .env.example .env  # 编辑填入 LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME 等

# 初始化知识库（Milvus 向量数据库）
python mediZJ/knowledge/scripts/import_hardcoded_data.py
python mediZJ/knowledge/scripts/deduplicate.py  # 数据去重

# 初始化会话数据库（SQLite + Milvus）
python mediZJ/memory/scripts/init_session_db.py          # 首次初始化
python mediZJ/memory/scripts/init_session_db.py --clean  # 清除后重新初始化

# 运行应用
python mediZJ/main.py          # 交互模式
python mediZJ/main.py -v       # 详细日志模式

# Web 模式（两个终端）
uv run python mediZJ/api_main.py                      # 后端 API，默认 8000 端口
cd frontend && npm install && npm run dev      # 前端，http://localhost:5173

# 运行测试（349 个单元测试 + 20 个集成测试）
# 单元测试默认执行；集成测试因依赖真实 LLM/Milvus/Mem0 默认跳过，
# 传 --run-integration 启用。集成测试依赖 .env 中的 LLM_API_KEY / LLM_BASE_URL 配置。
pytest tests/ -m "not integration"                     # 仅单元测试（快速，无需外部服务）
pytest tests/ -m "integration" --run-integration       # 仅集成测试（需要 .env 配置）
pytest tests/ --run-integration                        # 全部测试（349 个）
pytest tests/ -m "not integration" --cov --cov-report=html  # 覆盖率报告

# 运行评估（5 项指标）
python -m mediZJ.eval.runner --metrics all
python -m mediZJ.eval.runner --metrics routing,retrieval

# 前端构建与质量检查
cd frontend && npm run dev           # 开发服务器
cd frontend && npm run build         # 生产构建
cd frontend && npm run lint          # ESLint 检查
cd frontend && npm run lint:fix      # ESLint 自动修复
cd frontend && npm run format        # Prettier 格式化
cd frontend && npm run typecheck     # TypeScript 类型检查
cd frontend && npm run test          # 运行前端测试（Vitest）
cd frontend && npm run test:coverage # 前端测试覆盖率
```

## 架构设计

### 请求处理流程

```text
用户输入 → mediZJ/main.py / mediZJ/api_main.py → SwarmCoordinator
  │
  ├─ 意图识别（intent_classify 节点）
  │    ├─ others（寒暄/无关）→ chat_reply 闲聊直答（LeadAgent 直答并记录短期记忆）
  │    └─ medical → 进入澄清流程
  │
  ├─ LeadAgent.clarify()  ← 信息澄清：通过结构化问卷收集背景信息
  │    （clarify_decide ⇄ clarify_ask 多轮 interrupt，LLM 自决最多 3 轮）
  │
  ├─ 检索长短期记忆，构建增强上下文（retrieve_memories，先澄清再检索）
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
| `mediZJ/core/llm_client.py` | OpenAI 兼容的异步 LLM 客户端，支持流式、function calling |
| `mediZJ/core/agent_loop.py` | 核心执行引擎：Think-Act-Observe 循环，集成约束验证、自动修复、动态工具刷新、问卷暂停/恢复 |
| `mediZJ/core/circuit_breaker.py` | 进程级熔断器，跨请求累计 LLM 失败自动断开 |
| `mediZJ/core/stream_token_router.py` | 流式 token 路由 |
| `mediZJ/core/skill_registry.py` | 双层注册：Skill（能力包）+ Tool（底层函数），compat_mode 自动检测 |
| `mediZJ/core/skill_loader.py` | 从 `.claude/skills/` 动态发现技能，提取 SKILL.md 正文作为指令 |
| `mediZJ/core/skill_models.py` | `SkillDefinition` 数据模型 |
| `mediZJ/core/prompt_loader.py` | Jinja2 模板加载器，从 `mediZJ/prompt/` 目录加载模板 |
| `mediZJ/core/questionnaire_manager.py` | 问卷幂等校验/清理（interrupt 恢复由 SessionRuntime 承担） |
| `mediZJ/core/tools/activate_skill.py` | `activate_skill` 工具工厂 |
| `mediZJ/core/tools/questionnaire.py` | `question_for_user` 工具（XML 问卷解析） |
| `mediZJ/agents/base_agent.py` | Agent 抽象基类，集成 SkillRegistry + AgentLoop |
| `mediZJ/agents/skill_registry_mixin.py` | Worker Agent 共享的技能自动注册 Mixin |
| `mediZJ/swarm/swarm_coordinator.py` | 顶层协调器：意图分类 + 记忆检索 + 路由分发 + 并行调度（单次问答总超时由 `REQUEST_TIMEOUT` 控制） |
| `mediZJ/swarm/intent_classifier.py` | 意图识别门控（medical / others，失败降级 medical） |
| `mediZJ/swarm/lead_agent.py` | 闲聊直答 + 信息澄清 + 复杂度评估 + 任务分解 + 结果综合 |
| `mediZJ/swarm/shared_context.py` | 共享黑板系统（SubTask/Contribution 生命周期管理） |
| `mediZJ/swarm/events.py` | 事件驱动通信（16 种事件类型，含 AGENT_QUESTIONNAIRE） |
| `mediZJ/lgraph/supervisor_graph.py` | SupervisorGraph 主图：intent_classify → clarify ⇄ ask → retrieve → decompose → route → synthesize |
| `mediZJ/lgraph/agent_subgraph.py` | AgentSubGraph：Worker Think-Act-Observe 子图 |
| `mediZJ/lgraph/tool_registry.py` | 工具注册中心（allowed_agents 权限收口） |
| `mediZJ/lgraph/tool_executor.py` | 工具执行节点（约束验证 + references 收集） |
| `mediZJ/api/auth.py` | 免密登录认证（SQLite 随机会话令牌 + Cookie） |
| `mediZJ/api/services/session_runtime.py` | 会话级运行期：缓存 graph + MemorySaver，支持 interrupt 恢复 |
| `mediZJ/api/services/image_analyzer.py` | Vision 多模态图片解析（OCR 文本注入子任务） |
| `mediZJ/memory/short_term.py` | 短期记忆（单例，写时增量压缩，支持内存/Redis） |
| `mediZJ/memory/long_term.py` | 长期记忆（Mem0 云服务，经 LLM 质量门控过滤） |
| `mediZJ/memory/entropy_manager.py` | 熵管理器：向量语义去重 + LLM 摘要 + 截断降级 |
| `mediZJ/memory/session_db.py` | SQLite 会话数据库（sessions + messages + profiles 表） |
| `mediZJ/memory/session_vector_store.py` | Milvus 会话向量索引（session_summaries 集合） |
| `mediZJ/memory/personal_profile.py` | 个人健康档案（SQLite `profiles` 表，md 文本整体入库） |
| `mediZJ/memory/embedding.py` | 共享 embedding 工具（BAAI/bge-small-zh-v1.5，512 维） |
| `mediZJ/knowledge/milvus_kb.py` | Milvus Lite 向量知识库（单例）— 三路混合检索：Dense + BM25 + Entity Boost |
| `mediZJ/knowledge/entity_index.py` | 轻量级医学实体倒排索引：jieba 自抽取 + 内存映射，支持查询时精确命中加权 |
| `mediZJ/research/deep_research_workflow.py` | 多步骤研究流水线 |
| `mediZJ/constraints/validator.py` | 运行时约束验证（工具权限、输出质量） |
| `mediZJ/validation/auto_fixer.py` | 自动修复违规输出（高危警告、截断等） |
| `mediZJ/trace/` | 全链路追踪：Span 模型、收集器、聚合分析、SQLite 持久化 |

### 关键设计模式

- **单例模式**：`MedicalKnowledgeBase`、`ShortTermMemory`、`SessionDB`、`SessionVectorStore`（`__new__` 实现）
- **Mixin 模式**：`SkillRegistryMixin` 为所有 Worker Agent 提供共享的技能注册
- **共享黑板**：`SharedContext` 作为去中心化通信介质，Worker 自主认领任务
- **Harness Engineering**：非侵入式约束验证 + 自动修复注入 AgentLoop
- **事件驱动**：`Event` 系统 + `event_callback`（supervisor_graph 节点内回调 / SharedContext.on_event_callback）用于 SSE 流式推送

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

`MedicalEntityIndex`（`mediZJ/knowledge/entity_index.py`）：启动时从 Milvus 全量文档自抽取实体（中文字符 2-12 字、ICD 编码、药品后缀），构建 `entity → Set[doc_id]` 内存倒排；文档增删时增量同步。

### 知识库引用标注

LeadAgent 基于 RAG 结果生成回答时，检索 chunk 的句尾自动附加 `[N]` `[N,M]` 可点击引用标注。

**后端链路**：
- 三个 RAG Skill（`search-knowledge` / `clinical-guideline` / `deep-research`）统一返回结构化 `references` 数组：`[{index, doc_id, source, disease, type, filename, score, snippet, content}]`
- `ToolExecutor`（`mediZJ/lgraph/tool_executor.py`）工具执行后自动收集 references，按 `doc_id` 去重，附入 Worker 最终 result
- `SwarmCoordinator`：单 Agent 直接透传；Swarm 模式跨 Worker 收集 → 去重 → 重编号 → 替换贡献文本旧编号
- `synthesis.j2` 指示 LeadAgent 保留引用编号
- SS done / JSON 事件文件 / non-stream ChatResponse 三路径携带 `citations`

**前端渲染**：
- `useMarkdown.ts`：渲染后正则匹配 `[N]` `[N,M]` `[N-M]` 替换为 `<sup class="citation-ref">`
- `CitationPopover.vue`：Teleport 浮层，scroll/resize 实时跟随引用位置，外部点击关闭，固定高度滚动区展示 chunk 全文 + 来源/疾病/类型/相关度
- `ChatMessage.vue`：集成点击事件驱动 Popover

**持久化**：`messages` 表新增 `citations TEXT` 列（自动迁移），`save_turn` 写入 / `get_session` 反序列化；历史会话加载后引用标注仍可点击。

### Prompt 管理

所有 prompt 集中在 `mediZJ/prompt/` 目录，基于 Jinja2 模板引擎，23 个 `.j2` 模板分 6 个子目录 + 根级 `_language_rule.j2`（统一中文语言规则）：

```python
from mediZJ.core.prompt_loader import PromptLoader
system_prompt = PromptLoader.load("agents/consultation_system.j2")
user_msg = PromptLoader.render("swarm/assessment_user.j2", question="...", recent_history=[...])
```

### 会话持久化（SQLite + Milvus 双引擎）

每轮对话完成后自动持久化，三层回退加载（SQLite → .json → .md）。

- **SQLite**（`mediZJ/memory/data/sessions.db`）：结构化消息存储，事务原子写入
- **Milvus**（`mediZJ/memory/data/session_vectors.db`）：会话摘要向量索引，语义搜索
- **初始化**：`python mediZJ/memory/scripts/init_session_db.py`

### 记忆系统（三层）

| 层级 | 存储 | 用途 |
| --- | --- | --- |
| 短期记忆 | 内存（默认）/Redis | 会话级对话历史，写时增量压缩，仅供 LeadAgent 参考 |
| 个人档案 | `sessions.db` 的 `profiles` 表（content/pending 两列存 md 文本） | 患者信息（年龄/性别/病史/过敏史），AgentLoop 注入为 system message |
| 长期记忆 | Mem0 云服务 | 跨会话可复用医学事实，经 LLM 质量门控（score < 5 跳过） |

未设置 `MEM0_API_KEY` 时优雅降级，仅使用短期记忆和个人档案。

## 配置

环境变量（`.env`）：
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_NAME` — OpenAI 兼容 LLM 配置
- `LLM_TEMPERATURE`（默认 0.7）、`LLM_MAX_TOKENS`（默认 8192）
- `LLM_MAX_CONCURRENCY`（默认 16）— LLM 全局并发信号量
- `LLM_TIMEOUT`（默认 60s）、`REQUEST_TIMEOUT`（默认 300s）— 单次 LLM 请求与问答总超时
- `EMBEDDING_MODEL_NAME`（默认 `BAAI/bge-small-zh-v1.5`）
- `MEM0_API_KEY` — 可选，Mem0 长期记忆服务
- `MEDIZJ_ADMIN_USERNAME` / `AUTH_SESSION_DAYS` / `AUTH_COOKIE_SECURE` — 免密登录配置
- `VISION_MODEL_NAME` / `VISION_API_KEY` / `VISION_BASE_URL` — 可选，图片解析 Vision 模型（未设置回退主 LLM）
- `BASELINE_LLM_API_KEY` / `BASELINE_LLM_BASE_URL` / `BASELINE_LLM_MODEL_NAME` — AB 测试 Baseline 配置

约束定义（YAML）：
- `mediZJ/constraints/agent_constraints.yaml` — 各 Agent 能力边界、允许工具、禁止行为
- `mediZJ/constraints/swarm_constraints.yaml` — Swarm 协作规则、任务分解策略

## 前端工程化

### 目录结构

```text
frontend/
├── src/
│   ├── api/            # HTTP 请求层（axios 实例 + 各模块 API 函数：auth, chat, dashboard, image, knowledge, personal, session, trace）
│   ├── components/     # Vue 组件
│   │   ├── agents/     # Agent Timeline 等
│   │   ├── chat/       # 聊天相关（ChatMessage, ChatInput, ThinkingBlock, QuestionnaireCard, CitationPopover, SuggestionChips, DisclaimerBanner 等）
│   │   ├── layout/     # 布局（AppLayout, AppSidebar, AppHeader）
│   │   └── trace/      # 追踪（TraceTree, TraceWaterfall, SpanDetail 等）
│   ├── composables/    # 可组合函数（useSSE, useMarkdown）
│   ├── router/         # Vue Router 配置（含免密登录守卫）
│   ├── stores/         # Pinia Store（chat, auth, dashboard, knowledge, personal, trace）
│   ├── types/          # TypeScript 类型定义（含 SSE 事件类型体系）
│   ├── utils/          # 工具函数（eventAggregator, formatToolResult）
│   └── views/          # 页面视图（ChatView, KnowledgeView, SessionsView, DashboardView, PersonalView, TraceView）
├── eslint.config.mjs   # ESLint flat config
├── .prettierrc         # Prettier 配置
├── vitest.config.ts    # Vitest 测试配置
└── vite.config.ts      # Vite 构建配置（含 manualChunks 分包）
```

### 技术栈

- **框架**: Vue 3.5 + Composition API (`<script setup>`)
- **语言**: TypeScript 6.0（strict 模式）
- **构建**: Vite 8 + rolldown
- **状态管理**: Pinia 3
- **路由**: Vue Router 4
- **样式**: TailwindCSS 4 + 全局 CSS 变量
- **SSE**: fetch + ReadableStream 换行分隔 JSON 流

### 架构模式

**三层架构**：View → Store → API

- **View 层**：瘦组件，仅负责模板渲染与事件转发。所有状态和业务逻辑通过 Store 委派。
- **Store 层**（Pinia setup syntax）：管理各领域状态，封装 API 调用、loading/error 状态、CRUD 操作。
  - `chat.ts` — 聊天流式消息、SSE 事件、历史加载
  - `auth.ts` — 免密登录状态、会话令牌恢复
  - `dashboard.ts` — Dashboard 统计数据
  - `knowledge.ts` — 知识库搜索/文档管理
  - `personal.ts` — 个人健康档案 CRUD
  - `trace.ts` — 追踪/可观测性数据
- **API 层**：基于共享 axios 实例，RESTful 风格，统一返回类型标注。

### SSE 事件处理

**统一事件聚合器**（`src/utils/eventAggregator.ts`）是实时流与历史回放的唯一事件→UI状态转换入口：

- `createEventAggregator(isRealtime)` 工厂函数：`consume(eventType, data)` 消费事件 → `finalize()` 收尾补充 → `getSnapshot()` 获取 `{agentEvents, thinkingBlocks, delegations}`
- 实时流（`sendMessage`）：回调中 `consume` → 每次将 snapshot 写入响应式 `assistantMsg`
- 历史回放（`loadHistory`）：遍历 `rawEvents` → `consume` → `finalize` → 一次性获取完整 snapshot

避免了此前 sendMessage 回调和 reconstructFromEvents 函数间 6 种事件的重复处理逻辑。

### 类型安全

- `src/types/index.ts` 定义完整的 SSE 事件数据类型体系（`SSEEventType` 联合类型 + 各事件 `data` 接口）
- `useSSE.ts` 的 `StreamCallbacks` 每个回调参数均为具体类型，消除全部 `any`
- `AgentEvent.type` 使用字面量联合类型 `'decomposed' | 'start' | ... | 'complete'`

### 质量工具

| 工具 | 配置 | 命令 |
| ---- | ---- | ---- |
| ESLint | flat config（`@eslint/js` + `typescript-eslint` + `eslint-plugin-vue` + Prettier） | `npm run lint` / `lint:fix` |
| Prettier | `.prettierrc`（semi=false, singleQuote, trailingComma=all） | `npm run format` |
| Vitest | `vitest.config.ts`（jsdom, coverage v8） | `npm run test` / `test:coverage` |
| Git Hooks | `simple-git-hooks` + `lint-staged` | pre-commit: lint-staged, pre-push: typecheck + test |

### 构建优化

- `manualChunks`：`vendor-vue`（vue/pinia/vue-router，~90KB）+ `vendor-markdown`（markdown-it/dompurify，~124KB）
- `target: es2020`，production `sourcemap: false`

## 已知问题

- 后端代码无 linting/formatting/CI 配置（前端已完善）
