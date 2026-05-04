# MediZJ多智能体医疗助手

基于 Skills-Agent 两层架构的多智能体协作医疗助手系统，融合 Agent Loop、Agent Swarm、记忆管理、Milvus 知识库和 Web 前端界面。
![alt text](assets/image.png)

## 📋 项目概述

本项目采用创新的 **Skills-Agent 两层架构**，通过9个自包含的原子 Skills 和3个专业 Agent 协同工作，提供智能、专业的医疗服务。支持 **CLI 交互**和 **Web 界面**两种使用方式。

### 🎯 核心特性

- **🌐 Web 前端界面**: Vue 3 + FastAPI 全栈架构，支持智能问答、知识库浏览、会话管理、仪表盘 ✅
- **📡 流式响应**: 实时推送 Agent 协作过程，可视化多 Agent 并行执行 ✅
- **🔧 Skills 直达架构**: 9个原子 Skills 自包含，直接转换为 OpenAI function calling 格式 ✅
- **🤖 Agent Loop**: LLM 驱动的 Skill 调用循环，Agent 自主规划、调用 Skills 并完成任务 ✅
- **🐝 Agent Swarm**: 真正的群体智能（去中心化协作，自主任务认领，并行执行）✅
- **🧠 记忆系统**: 短期记忆（会话级对话历史）+ 长期记忆（Mem0跨会话记忆）+ **多轮对话上下文利用** + **LLM 语义摘要压缩** ✅
- **💾 Milvus 知识库**: 统一知识管理，语义检索，支持模糊查询（"血压高" → "高血压"）；Web 界面支持文档增删改查、文件上传、chunk 查看 ✅
- **⚡ Claude Code Skills**: 9个预定义技能，一键调用医疗助手 ✅
- **🏗️ Harness Engineering**: 约束驱动 + 熵管理，系统自动验证和优化，保证安全、简洁、高质量 ✅
- **📝 Prompt 集中管理**: 所有 prompt 统一存放在 `prompt/` 目录，基于 Jinja2 模板引擎管理，支持变量渲染和条件分支 ✅

## 🎯 Skills 直达架构

### 架构设计

```
Skills (函数) → 直接转换 → OpenAI Format → LLM 调用
         ↓
    Milvus/业务逻辑
```

### 关键特性

1. **Skills 直达 LLM**
   - Skill 函数直接转换为 OpenAI function calling 格式
   - SkillRegistry 统一管理：注册、执行、格式转换

2. **简化的注册流程**
   ```python
   skill → OpenAI Format
   ```

3. **Agent 灵活选择**
   - 每个 Agent 注册全部7个 Skills
   - Agent Loop 根据任务自主选择合适的 Skills
   - 一个 Agent 可以跨领域调用 Skills

4. **用户友好入口**
   - 7个原子 Skills：快速查询，立即响应
   - 1个复杂 Skill：触发 Swarm 协作
   - 用户无需理解 Agent 架构

5. **多轮对话支持**
   - 短期记忆：会话级对话历史（10条消息）
   - 长期记忆：Mem0 跨会话记忆
   - **上下文利用率 100%**：追问能正确理解历史对话
   - **LLM 语义摘要**：早期对话自动压缩为结构化摘要，保留关键医学信息


## 🚀 从零开始运行

### 1. 环境准备

```bash
conda create -n medix-swarm python=3.12 -y
conda activate medix-swarm
cd medix-agent-swarm
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API

复制 `.env.example` 并填入实际配置：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# LLM 配置（OpenAI 兼容 API）
LLM_API_KEY=your-llm-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=your-model-name
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=8192

# Mem0 长期记忆配置（可选）
MEM0_API_KEY=m0-your-api-key-here
```

### 4. 初始化知识库

```bash
python knowledge/scripts/import_hardcoded_data.py
```

### 5. 运行测试

```bash
python examples/test_all.py
```

### 6. 开始使用

**方式一：Web 界面（推荐）**

```bash
# 终端 1：启动后端 API 服务
uv run python api_main.py

# 终端 2：启动前端开发服务器
cd frontend && npm install && npm run dev

# 浏览器访问 http://localhost:5173
# API 文档：http://localhost:8000/docs
```

**方式二：CLI 交互**

```bash
python main.py
```

## 📦 项目结构

```
medix-agent-swarm/
├── api/                               # FastAPI 后端 API 层
│   ├── main.py                        # FastAPI 应用入口、CORS
│   ├── routers/
│   │   ├── chat.py                    # /api/chat 问答接口（含流式）
│   │   ├── knowledge.py               # /api/knowledge 知识库检索
│   │   ├── sessions.py                # /api/sessions 会话管理
│   │   └── dashboard.py               # /api/dashboard 仪表盘 + 健康检查
│   ├── models/                        # Pydantic 请求/响应模型
│   ├── services/                      # 业务逻辑封装
│   └── dependencies.py               # 依赖注入
│
├── frontend/                          # Vue 3 前端项目
│   ├── src/
│   │   ├── views/                     # 页面：ChatView, KnowledgeView（搜索/文档管理/上传）, SessionsView, DashboardView
│   │   ├── components/                # 组件：chat/, agents/, knowledge/, dashboard/, layout/
│   │   ├── stores/                    # Pinia 状态管理
│   │   ├── api/                       # API 调用层
│   │   ├── composables/               # useSSE (流式), useMarkdown
│   │   └── types/                     # TypeScript 类型定义
│   ├── vite.config.ts
│   └── package.json
│
├── .claude/skills/                    # Claude Code Skills (9个)
│   ├── search-knowledge/              # 搜索医学知识库
│   ├── assess-risk/                   # 风险评估
│   ├── analyze-symptoms/              # 症状分析
│   ├── recommend-lifestyle/           # 生活方式建议
│   ├── disease-code/                  # ICD-10疾病编码
│   ├── clinical-guideline/            # 临床指南检索
│   ├── deep-research/                 # 深度研究
│   ├── search-history/                # 搜索会话历史（短期记忆）
│   └── search-similar-cases/          # 搜索相似案例（长期记忆）
│
├── agents/                            # Agent 实现
│   ├── base_agent.py                  # Agent 基类
│   ├── consultation_agent.py          # 健康咨询 Agent
│   ├── diagnostic_agent.py            # 症状诊断 Agent
│   ├── research_agent.py              # 医学研究 Agent
│   └── skill_registry_mixin.py        # Skill 注册混入
│
├── core/                              # 核心引擎
│   ├── agent_loop.py                  # Agent Loop（集成约束验证）
│   ├── llm_client.py                  # LLM 客户端
│   ├── prompt_loader.py               # Jinja2 Prompt 模板加载器
│   ├── skill_loader.py                # 动态加载 Skills
│   ├── skill_registry.py              # Skill 注册表（直接转 OpenAI format）
│   └── state_manager.py               # 状态管理
│
├── prompt/                            # Prompt 模板（Jinja2，集中管理）
│   ├── agents/                        # Agent 系统提示词
│   │   ├── consultation_system.j2
│   │   ├── consultation_user_input.j2
│   │   ├── diagnostic_system.j2
│   │   └── research_system.j2
│   ├── swarm/                         # Swarm 协调相关提示词
│   │   ├── lead_system.j2
│   │   ├── synthesis.j2
│   │   ├── assessment_user.j2
│   │   └── timeout_fallback.j2
│   ├── research/                      # 研究模块提示词
│   │   ├── evidence_synthesis.j2
│   │   └── query_planning.j2
│   ├── memory/                        # 记忆压缩提示词
│   │   ├── compression_system.j2
│   │   └── compression_user.j2
│   ├── agent_loop/                    # Agent Loop 控制消息
│   │   ├── tool_limit.j2
│   │   └── force_answer.j2
│   └── validation/                    # 输出验证模板
│       ├── disclaimer.j2
│       ├── high_risk_warning.j2
│       ├── truncation_notice.j2
│       └── swarm_disclaimer.j2
│
├── swarm/                             # Swarm 协调器
│   ├── events.py                      # 事件驱动通信
│   ├── lead_agent.py                  # 任务分解和汇总
│   ├── shared_context.py              # 共享环境（信息素）
│   └── swarm_coordinator.py           # 智能路由
│
├── memory/                            # 记忆管理（集成熵管理）
│   ├── long_term.py                   # 长期记忆（Mem0）
│   ├── short_term.py                  # 短期记忆（单例，集成 LLM 语义摘要）
│   ├── session_summary.py             # 会话总结
│   └── entropy_manager.py             # 熵管理器（LLM 摘要 + 截断降级）
│
├── constraints/                       # 约束系统
│   ├── agent_constraints.yaml         # Agent 能力边界
│   ├── swarm_constraints.yaml         # Swarm 协作规则
│   └── validator.py                   # 约束验证器
│
├── validation/                        # 输出验证和修复
│   └── auto_fixer.py                  # 自动修复器
│
├── knowledge/                         # Milvus 知识库
│   ├── milvus_kb.py                   # 知识库封装（CRUD + 语义搜索）
│   ├── data/documents/                # 医学知识文档
│   └── scripts/
│       ├── import_hardcoded_data.py   # 批量导入文档
│       └── deduplicate.py             # 数据去重脚本
│
├── research/                          # DeepResearch 模块
│   ├── deep_research_workflow.py
│   ├── evidence_synthesizer.py
│   └── web_search.py
│
├── examples/
│   └── test_all.py                    # 测试套件
│
├── main.py                            # CLI 入口
├── api_main.py                        # Web 服务入口（uvicorn）
├── pyproject.toml                     # 项目配置和依赖
└── .env                               # 环境变量配置
```

**架构说明**：
- ✅ **Web + CLI 双入口**：`api_main.py`（Web）/ `main.py`（CLI）
- ✅ **直达架构**：Skills → OpenAI Format
- ✅ **Skills 自包含**：每个 Skill 在 `script/` 目录下实现，直接调用知识库
- ✅ **动态加载**：`skill_loader.py` 扫描 `.claude/skills/` 目录动态加载
- ✅ **SkillRegistry**：统一管理 Skill 注册、执行、格式转换
- ✅ **统一配置**：使用项目根目录 `.env` 文件管理环境变量
- ✅ **记忆分离**：Agent 身份文件和会话总结分别存储在 `memory/agents/` 和 `memory/swarm/`

## 🤖 Skills 和 Agent 清单

### 9个原子 Skills（两层架构）

**所有 Agent 共享以下 Skills**：

| Skill | 功能 | 数据源 | 特点 |
|-------|------|--------|------|
| `search_knowledge` | 搜索医学知识库 | Milvus | 语义检索 |
| `recommend_lifestyle` | 生活方式和用药建议 | Milvus | 个性化建议 |
| `assess_risk` | 风险等级评估 | 规则引擎 | 高危症状识别 |
| `analyze_symptoms` | 症状模式分析 | 规则引擎 | 多系统分析 |
| `disease_code` | ICD-10疾病编码 | Milvus | 标准编码 |
| `clinical_guideline` | 临床指南检索 | Milvus | 权威指南 |
| `deep_research` | 深度研究 | 网络搜索 | 最新进展 |
| `search_history` | 搜索会话历史 | 短期记忆 | 当前会话上下文 |
| `search_similar_cases` | 搜索相似案例 | 长期记忆 | 跨会话经验 |

### 3个专业 Agent（自主选择 Skills）

#### 1. ConsultationAgent（健康咨询）
- **能力**: 通用健康咨询和生活方式指导
- **注册 Skills**: 全部7个（自主选择合适的 Skills）
- **常用 Skills**: `search_knowledge`, `recommend_lifestyle`

#### 2. DiagnosticAgent（症状诊断）
- **能力**: 症状分析、风险评估和鉴别诊断
- **注册 Skills**: 全部7个（自主选择合适的 Skills）
- **常用 Skills**: `assess_risk`, `analyze_symptoms`, `disease_code`

#### 3. ResearchAgent（医学研究）
- **能力**: 循证医学证据和权威指南检索
- **注册 Skills**: 全部7个（自主选择合适的 Skills）
- **常用 Skills**: `clinical_guideline`, `deep_research`

### 2个协调 Agent

- **LeadAgent**: 任务分解和结果汇总（非编排器）
- **SwarmCoordinator**: 智能路由（简单问题→单Agent，复杂问题→Swarm）

### Skills 架构特点

- ✅ **直达架构**: Skills → OpenAI Format
- ✅ **Skills 自包含**: 直接调用 Milvus 或内置逻辑
- ✅ **Agent 灵活性**: 每个 Agent 注册全部9个 Skills，根据任务自主选择
- ✅ **SkillRegistry**: 统一管理注册、执行、格式转换
- ✅ **统一知识库**: 医学知识统一存储在 Milvus 向量数据库，支持语义检索
- ✅ **易于扩展**: 添加新 Skill 或新知识无需修改 Agent 代码

## 🌐 Web 界面

系统提供基于 Vue 3 + FastAPI 的 Web 界面，支持以下功能模块：

| 页面 | 功能 | 路由 |
|------|------|------|
| **智能问答** | 聊天式问答，实时展示 Agent 协作过程，Markdown 渲染 | `/chat` |
| **知识库** | 医学知识语义搜索、文档管理（增删改查）、文件上传、chunk 查看 | `/knowledge` |
| **历史会话** | 会话列表查看、恢复、删除 | `/sessions` |
| **仪表盘** | 统计概览、Agent 使用分布、最近会话 | `/dashboard` |

### Web 架构

```
浏览器 (Vue 3 + Vite + Tailwind CSS)
   ↓ fetch + ReadableStream
Vite Dev Proxy (/api → localhost:8000)
   ↓
FastAPI (api_main.py)
   ↓
api/routers/chat.py → api/services/chat_service.py
   ↓ EventBridge (asyncio.Queue)
SwarmCoordinator.process()
   ↓
SharedContext.on_event_callback → 事件推送
   ↓
换行分隔 JSON 流式响应 → 前端实时渲染
```

### API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/chat` | 非流式问答 |
| POST | `/api/chat/stream` | 流式问答（换行分隔 JSON） |
| GET | `/api/chat/history/{session_id}` | 获取会话历史 |
| POST | `/api/knowledge/search` | 知识库搜索（去重，返回完整文档） |
| GET | `/api/knowledge/types` | 知识库类型列表 |
| GET | `/api/knowledge/documents` | 文档列表 |
| GET | `/api/knowledge/documents/{doc_id}/chunks` | 查看文档分块 |
| DELETE | `/api/knowledge/documents/{doc_id}` | 删除文档 |
| POST | `/api/knowledge/upload` | 上传文件（.txt） |
| PUT | `/api/knowledge/documents/{doc_id}` | 更新文档内容 |
| GET | `/api/sessions` | 会话列表 |
| GET | `/api/sessions/{session_id}` | 会话详情 |
| DELETE | `/api/sessions/{session_id}` | 删除会话 |
| GET | `/api/dashboard/stats` | 仪表盘统计 |
| GET | `/api/health` | 健康检查 |


## ⚙️ 配置说明

项目使用 `.env` 文件管理配置（基于 `python-dotenv`）。

### 配置内容

```env
# LLM 配置（OpenAI 兼容 API）
LLM_API_KEY=your-llm-api-key
LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
LLM_MODEL_NAME=your-model
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=8192

# Mem0 长期记忆配置（可选，获取地址：https://app.mem0.ai）
MEM0_API_KEY=m0-your-api-key-here
```

### 记忆系统配置

本系统支持两层记忆机制：**短期记忆（会话级）** 和 **长期记忆（跨会话）**。

#### 短期记忆（ShortTermMemory）

**作用**：存储当前会话的对话历史，支持多轮对话上下文理解。

**配置**：
```python
# 方式1: 内存存储（默认，无需配置）
from memory.short_term import ShortTermMemory
memory = ShortTermMemory(session_id="user_123", storage_type="memory")

# 方式2: Redis持久化（可选）
memory = ShortTermMemory(session_id="user_123", storage_type="redis")
```

**使用示例**：
```python
# 添加消息
memory.add_message(role="user", content="我有高血压")
memory.add_message(role="assistant", content="高血压需要...")

# 获取对话历史（最近10条）
history = memory.get_messages(limit=10)

# 会话结束时清空
memory.clear()
```

**存储方式**：
- **内存**（默认）：无需配置，保留时间60分钟
- **Redis**（可选）：通过 `redis_config` 参数传入配置，支持持久化
- 存储容量：最近10条消息

**智能压缩**：

短期记忆集成了熵管理器，在消息积累过多时自动压缩早期对话：

```python
# 初始化时传入 llm_client 启用 LLM 语义摘要
from memory.short_term import ShortTermMemory
from core.llm_client import LLMClient

memory = ShortTermMemory(storage_type="memory", llm_client=LLMClient())
```

- **LLM 语义摘要**（推荐）：调用 LLM 将早期对话压缩为结构化摘要，保留症状、诊断、用药等关键医学信息
- **截断降级**（自动）：LLM 不可用或调用失败时，自动降级为截断模式
- **去重**：基于 MD5 哈希检测并移除重复消息

#### 长期记忆（Mem0）

**作用**：跨会话记忆，通过向量相似度检索历史案例和经验。

**配置**：

```env
# 在 .env 文件中配置
MEM0_API_KEY=m0-your-api-key-here  # 获取地址：https://app.mem0.ai
```

**使用示例**：
```python
from memory.long_term import LongTermMemory
memory = LongTermMemory(user_id="user_123")

# 存储会话总结
memory.add("患者有高血压，给出了生活方式建议")

# 检索相关记忆
results = memory.search("高血压患者如何管理？")
# → 返回历史相似案例
```

**存储方式**：
- **Mem0 云服务**：自动处理向量化和相似度搜索
- 存储范围：跨会话持久化
- 存储内容：会话总结
- 无需本地部署向量数据库

#### 记忆系统如何融入对话

**流程**：

```
1. 会话开始
   ↓
2. 从 Mem0 检索相关长期记忆（历史案例）
   ↓
3. 初始化短期记忆（对话历史）
   ↓
4. Agent 执行
   - 读取短期记忆：获取当前会话上下文
   - 写入短期记忆：记录本轮对话
   - 参考长期记忆：利用历史经验
   ↓
5. 会话结束
   ↓
6. 短期记忆转换为结构化数据 → 存入 Mem0 长期记忆
   ↓
7. 清空短期记忆
```

**多轮对话示例**：

```python
# 第1轮
用户: "我有高血压"
系统: [短期记忆添加用户消息]
系统: [Agent 处理] "高血压需要注意..."
系统: [短期记忆添加助手消息]

# 第2轮
用户: "那我应该吃什么药？"  # 追问
系统: [读取短期记忆] → 获取上一轮"高血压"上下文
系统: [Agent 处理] "根据您的高血压情况，建议..."  # 正确理解追问
```

**注意事项**：

- 未设置 `MEM0_API_KEY` 时，系统会优雅降级，仅使用短期记忆继续工作
- 短期记忆默认使用内存存储，无需配置 Redis
- 长期记忆依赖 Mem0 云服务，需注册账号获取 API Key

## 🏗️ Harness Engineering 融合

**核心理念**："人类设计约束，AI 代理执行" —— 让 AI 在明确约束下自主工作、自我修正。

### 实现的 Harness 原则

| 原则 | MediZJ 实现 | 位置 |
|------|-----------|------|
| **约束驱动** | YAML 定义 Agent 能力边界，运行时验证 | `constraints/` |
| **自动修复** | 输出违规自动添加免责声明、高危警告 | `validation/` |
| **熵管理** | 记忆自动去重和压缩，防止系统膨胀 | `memory/entropy_manager.py` |

### 核心功能

**1. 约束验证**（`constraints/agent_constraints.yaml`）
- 定义每个 Agent 允许的 Skills 和禁止的行为
- 运行时自动验证 Skill 调用和输出内容
- 违规时记录警告日志

**2. 自动修复**（`validation/auto_fixer.py`）
- 缺少免责声明 → 自动添加
- 高危症状（胸痛、呼吸困难等）→ 自动添加就医提醒

**3. 熵管理**（`memory/entropy_manager.py`）
- 自动去重重复消息（基于 MD5 哈希）
- 自动压缩历史对话：LLM 语义摘要（保留关键医学信息）+ 截断降级（LLM 不可用时自动切换）
- 熵估算和优化建议

### 验证

运行完整测试套件（包含 Harness 测试）：
```bash
python examples/test_all.py
```

---

## 📝 Prompt 集中管理

所有 LLM prompt 统一存放在 `prompt/` 目录下，使用 Jinja2 模板引擎管理，实现 prompt 与代码解耦。

### 设计理念

- **集中管理**: 18 个 `.j2` 模板文件按功能分 6 个子文件夹，所有 prompt 一目了然
- **Jinja2 模板**: 支持变量渲染（`{{ variable }}`）、条件分支（`{% if %}`）、循环（`{% for %}`）
- **代码解耦**: Python 代码不再包含 prompt 字符串，修改 prompt 无需改动业务逻辑
- **统一入口**: `PromptLoader` 类提供 `load()`（静态）和 `render()`（带变量）两个方法

### 模板目录结构

```
prompt/
├── agents/                # Agent 系统提示词（4 个）
├── swarm/                 # Swarm 协调提示词（4 个）
├── research/              # 研究模块提示词（2 个）
├── memory/                # 记忆压缩提示词（2 个）
├── agent_loop/            # Agent Loop 控制消息（2 个）
└── validation/            # 输出验证模板（4 个）
```

### 使用方式

```python
from core.prompt_loader import PromptLoader

# 加载静态模板（无变量）
system_prompt = PromptLoader.load("agents/consultation_system.j2")

# 渲染带变量的模板
user_msg = PromptLoader.render(
    "swarm/assessment_user.j2",
    question="高血压怎么办？",
    context="无"
)

# 带条件分支的模板
disclaimer = PromptLoader.render(
    "validation/swarm_disclaimer.j2",
    timeout_occurred=True,
    completed_agents_count=1
)
```

### 模板变量说明

| 模板 | 变量 | 说明 |
| --- | --- | --- |
| `agents/consultation_user_input.j2` | `question`, `session_id`, `context` | 用户输入格式化 |
| `swarm/synthesis.j2` | `question`, `contributions_text`, `timeout_note`, `timeout_occurred` | 多 Agent 结果综合 |
| `swarm/assessment_user.j2` | `question`, `context` | LeadAgent 任务评估 |
| `research/evidence_synthesis.j2` | `query`, `web_results`, `kb_results` | 证据综合（含 for 循环） |
| `research/query_planning.j2` | `question` | 查询拆解 |
| `memory/compression_user.j2` | `dialogue_text` | 对话压缩 |
| `agent_loop/tool_limit.j2` | `max_tool_calls` | 工具调用上限 |
| `validation/swarm_disclaimer.j2` | `timeout_occurred`, `completed_agents_count` | Swarm 免责声明 |

---

## 📚 统一知识库

- **向量数据库**: Milvus Lite（本地文件，无需服务器）
- **Embedding 模型**: BAAI/bge-small-zh-v1.5（中文，512维）
- **数据存储**: `knowledge/data/documents/` (txt 文档)
- **初始化**: `python knowledge/scripts/import_hardcoded_data.py`

### 知识库管理功能

Web 界面的知识库页面提供三个 Tab：

- **搜索**: 语义搜索，按 `doc_id` 去重，返回完整文档内容
- **文档管理**: 查看所有文档列表、每个文档的 chunk 详情、编辑和删除文档
- **上传文件**: 拖拽上传 `.txt` 文件，选择文档类型和元数据

### 数据去重

如因多次导入导致数据重复，运行去重脚本：

```bash
python knowledge/scripts/deduplicate.py
```


## 🤝 技术架构

### Agent Loop (Think-Act-Observe)

```
┌─────────┐     ┌────────┐     ┌──────────┐
│  Think  │ ──> │  Act   │ ──> │  Observe │
└─────────┘     └────────┘     └──────────┘
     ↑                               │
     └───────────────────────────────┘
```

### Skills 直达架构

```
用户问题
   ↓
【原子查询】→ 直接调用 Skills → Milvus/业务逻辑
   │                ↓
   │         OpenAI Format
   │
   └─【复杂问题】
          ↓
   SwarmCoordinator（智能路由）
          ↓
     LeadAgent（分解任务）
          ↓
    发布到 SharedContext（共享环境）
          ↓
    ┌─────┴─────┬────────┐
    ↓           ↓        ↓
ConsultAgent DiagAgent ResearchAgent
（SkillRegistry）（直达 LLM）（并行执行）
    │           │        │
    └───────────┴────────┘
          ↓
    LeadAgent（汇总结果）
          ↓
   SessionSummary（学习）
```

**核心原理**：
- ✅ Skills → OpenAI Format
- ✅ SkillRegistry 统一管理（注册、执行、转换）
- ✅ Agent 注册所有 Skills，根据任务自主选择
- ✅ Agent 通过"信息素"（SharedContext）间接通信
- ✅ 去中心化协作，整体能力涌现

### Agent Swarm 群体智能

**关键特性**：去中心化、自组织、涌现智能

**工作流程**：
1. 简单问题 → 单 Agent（快速响应）
2. 复杂问题 → LeadAgent 分解任务
3. Worker Agents 自主认领（基于能力匹配）
4. 并行执行（每个 Agent 自主选择 Skills）
5. LeadAgent 汇总结果
6. SessionSummary 学习总结

### 记忆系统架构

```
┌────────────────────────────────────┐
│  短期记忆（会话级，内存/Redis）     │
│  - 对话历史（messages）            │
│  - 当前会话上下文                  │
│  - 保留时间：60分钟                │
│  存储：内存（默认）或 Redis        │
├────────────────────────────────────┤
│  熵管理器（自动优化）              │
│  - MD5 去重（检测重复消息）        │
│  - LLM 语义摘要（压缩早期对话）    │
│  - 截断降级（LLM 不可用时）        │
└────────────────────────────────────┘
           ↕ (会话结束时)
┌────────────────────────────────────┐
│  长期记忆（跨会话，Mem0云服务）    │
│  - 会话总结                        │
│  存储：Mem0 API + 向量数据库       │
└────────────────────────────────────┘
```

## ⚠️ 免责声明

本系统仅供学习和研究使用，不能替代专业医生的诊断和治疗。所有医疗建议仅供参考，如有健康问题请及时就医。

## 📄 许可证

MIT License

## 🙏 致谢

- 使用 [LLM API](https://www.volcengine.com/) 作为LLM后端
- 记忆管理基于 [Mem0](https://mem0.ai/)

---
