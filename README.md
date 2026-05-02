# MediX多智能体医疗助手

基于 Skills-Agent 两层架构的多智能体协作医疗助手系统，融合 Agent Loop、Agent Swarm、记忆管理和 Milvus 知识库。

## 📋 项目概述

本项目采用创新的 **Skills-Agent 两层架构**，通过7个自包含的原子 Skills 和3个专业 Agent 协同工作，提供智能、专业的医疗服务。

### 🎯 核心特性

- **🔧 Skills 直达架构**: 7个原子 Skills 自包含，直接转换为 OpenAI function calling 格式 ✅
- **🤖 Agent Loop**: LLM 驱动的 Skill 调用循环，Agent 自主规划、调用 Skills 并完成任务 ✅
- **🐝 Agent Swarm**: 真正的群体智能（去中心化协作，自主任务认领，并行执行）✅
- **🧠 记忆系统**: 短期记忆（会话级对话历史）+ 长期记忆（Mem0跨会话记忆）+ **多轮对话上下文利用** ✅
- **💾 Milvus 知识库**: 统一知识管理，语义检索，支持模糊查询（"血压高" → "高血压"）✅
- **⚡ Claude Code Skills**: 8个预定义技能（7个原子 + 1个复杂），一键调用医疗助手 ✅
- **🏗️ Harness Engineering**: 约束驱动 + 熵管理，系统自动验证和优化，保证安全、简洁、高质量 ✅

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

### 测试验证

**所有测试通过（26个测试用例，100% 通过率）**：

**核心功能**：
- ✅ Agent Loop 和 Skill 调用
- ✅ Agent Swarm 群体智能
- ✅ 记忆系统（短期+长期）
- ✅ 多轮对话上下文利用 🌟
- ✅ Skills 自主选择
- ✅ Milvus 知识库集成

**Harness Engineering（Phase 8）**：
- ✅ 约束系统（Skill 调用验证、输出验证、自动修复）
- ✅ 熵管理（去重、压缩、熵估算）
- ✅ Agent Loop 集成（约束 + 熵管理协同工作）

运行测试套件：
```bash
# 运行所有测试（包含 Harness Engineering）
python examples/test_all.py
```

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

创建 `../config.py`：

```python
LLM_CONFIG = {
    "api_key": "your-llm-api-key",
    "model_name": "your-model-name",
    "base_url": "https://api.openai.com/v1",
    "temperature": 0.7,
    "max_tokens": 8192,
}

MEM0_CONFIG = {"api_key": "m0-your-api-key-here"}
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

```bash
python main.py
```

## 📦 项目结构

```
medix-agent-swarm/
├── .claude/skills/                    # Claude Code Skills (10个)
│   ├── search-knowledge/              # 搜索医学知识库
│   │   └── script/
│   │       ├── __init__.py
│   │       └── search.py
│   ├── assess-risk/                   # 风险评估
│   │   └── script/
│   │       ├── __init__.py
│   │       └── risk.py
│   ├── analyze-symptoms/              # 症状分析
│   │   └── script/
│   │       ├── __init__.py
│   │       └── symptoms.py
│   ├── recommend-lifestyle/           # 生活方式建议
│   │   └── script/
│   │       ├── __init__.py
│   │       └── lifestyle.py
│   ├── disease-code/                  # ICD-10疾病编码
│   │   └── script/
│   │       ├── __init__.py
│   │       └── code.py
│   ├── clinical-guideline/            # 临床指南检索
│   │   └── script/
│   │       ├── __init__.py
│   │       └── guideline.py
│   ├── deep-research/                 # 深度研究
│   │   └── script/
│   │       ├── __init__.py
│   │       └── research.py
│   ├── search-history/                # 搜索会话历史（短期记忆）
│   │   └── script/search.py
│   └── search-similar-cases/          # 搜索相似案例（长期记忆）
│       └── script/search.py
│
├── agents/                            # Agent 实现
│   ├── __init__.py
│   ├── base_agent.py                  # Agent 基类
│   ├── consultation_agent.py          # 健康咨询 Agent
│   ├── diagnostic_agent.py            # 症状诊断 Agent
│   ├── research_agent.py              # 医学研究 Agent
│   └── skill_registry_mixin.py        # Skill 注册混入
│
├── core/                              # 核心引擎
│   ├── __init__.py
│   ├── agent_loop.py                  # Agent Loop（集成约束验证）
│   ├── llm_client.py                  # LLM 客户端
│   ├── skill_loader.py                # 动态加载 Skills
│   ├── skill_registry.py              # Skill 注册表（直接转 OpenAI format，无 Tool 层）
│   └── state_manager.py               # 状态管理
│
├── swarm/                             # Swarm 协调器
│   ├── __init__.py
│   ├── events.py                      # 事件驱动通信
│   ├── lead_agent.py                  # 任务分解和汇总
│   ├── shared_context.py              # 共享环境（信息素）
│   └── swarm_coordinator.py           # 智能路由
│
├── memory/                            # 记忆管理（集成熵管理）
│   ├── __init__.py
│   ├── agent_identity.py              # Agent 身份管理
│   ├── long_term.py                   # 长期记忆（Mem0）
│   ├── short_term.py                  # 短期记忆（自动去重和压缩）
│   ├── session_summary.py             # 会话总结
│   ├── entropy_manager.py             # 熵管理器（Harness）
│   ├── agents/                        # Agent 身份文件存储
│   └── swarm/session_summaries/       # Swarm 会话总结存储
│
├── constraints/                       # 约束系统（Harness）
│   ├── __init__.py
│   ├── agent_constraints.yaml         # Agent 能力边界定义
│   ├── swarm_constraints.yaml         # Swarm 协作规则
│   └── validator.py                   # 运行时约束验证器
│
├── validation/                        # 输出验证和修复（Harness）
│   ├── __init__.py
│   └── auto_fixer.py                  # 自动修复器
│
├── knowledge/                         # Milvus 知识库
│   ├── __init__.py
│   ├── milvus_kb.py                   # 知识库封装
│   ├── data/
│   │   ├── documents/                 # 医学知识文档（txt）
│   │   └── milvus_lite.db             # 向量数据库（自动生成）
│   └── scripts/
│       ├── __init__.py
│       └── import_hardcoded_data.py   # 数据导入脚本
│
├── research/                          # DeepResearch 模块
│   ├── __init__.py
│   ├── deep_research_workflow.py      # 深度研究工作流
│   ├── evidence_synthesizer.py        # 证据综合器
│   ├── knowledge_base.py              # 知识库（可选 Qdrant）
│   └── web_search.py                  # 网络搜索
│
├── examples/
│   └── test_all.py                    # 完整测试套件（26个测试）
│
├── main.py                            # 主入口（交互式对话）
├── setup.py                           # 安装脚本
├── requirements.txt                   # 依赖列表
├── MediX代码解读.md                   # 代码解读文档
└── README.md                          # 本文档
```

**架构说明**：
- ✅ **直达架构**：Skills → OpenAI Format
- ✅ **Skills 自包含**：每个 Skill 在 `script/` 目录下实现，直接调用知识库
- ✅ **动态加载**：`skill_loader.py` 扫描 `.claude/skills/` 目录动态加载
- ✅ **SkillRegistry**：统一管理 Skill 注册、执行、格式转换
- ✅ **统一配置**：使用上层 `/Users/saintgeo/Desktop/self-learn/swarm/config.py`
- ✅ **记忆分离**：Agent 身份文件和会话总结分别存储在 `memory/agents/` 和 `memory/swarm/`

## 🤖 Skills 和 Agent 清单

### 7个原子 Skills（两层架构）

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
- ✅ **Agent 灵活性**: 每个 Agent 注册全部7个 Skills，根据任务自主选择
- ✅ **SkillRegistry**: 统一管理注册、执行、格式转换
- ✅ **统一知识库**: 医学知识统一存储在 Milvus 向量数据库，支持语义检索
- ✅ **易于扩展**: 添加新 Skill 或新知识无需修改 Agent 代码


## ⚙️ 配置说明

项目使用上层目录的统一配置文件：`/Users/saintgeo/Desktop/self-learn/swarm/config.py`

### 配置内容

```python
# LLM API config (LLM)
LLM_CONFIG = {
    "api_key": "your-your-model-api-key",
    "model_name": "your-model",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.7,
    "max_tokens": 8192,
}

# Mem0 API config (Long-term memory)
MEM0_CONFIG = {
    "api_key": "m0-your-api-key-here",  # 获取地址：https://app.mem0.ai
}

# Redis config (Short-term memory persistence, optional)
REDIS_CONFIG = {
    "host": "localhost",
    "port": 6379,
    "db": 0,
}
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
- **Redis**（可选）：需配置 REDIS_CONFIG，支持持久化
- 存储容量：最近10条消息

#### 长期记忆（Mem0）

**作用**：跨会话记忆，通过向量相似度检索历史案例和经验。

**配置**：
```python
# 在 /Users/saintgeo/Desktop/self-learn/swarm/config.py 中配置
MEM0_CONFIG = {
    "api_key": "m0-your-api-key-here",  # 获取地址：https://app.mem0.ai
}
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
- 未设置 `MEM0_CONFIG["api_key"]` 时，系统会优雅降级，仅使用短期记忆继续工作
- 短期记忆默认使用内存存储，无需配置 Redis
- 长期记忆依赖 Mem0 云服务，需注册账号获取 API Key

## 🏗️ Harness Engineering 融合

**核心理念**："人类设计约束，AI 代理执行" —— 让 AI 在明确约束下自主工作、自我修正。

### 实现的 Harness 原则

| 原则 | MediX 实现 | 位置 |
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
- 自动压缩历史对话（保留最近消息，压缩早期消息为摘要）
- 熵估算和优化建议

### 验证

运行完整测试套件（包含 Harness 测试）：
```bash
python examples/test_all.py
```

---

## 📚 统一知识库

- **向量数据库**: Milvus Lite（本地文件，无需服务器）
- **Embedding 模型**: BAAI/bge-small-zh-v1.5（中文，512维）
- **数据存储**: `knowledge/data/documents/` (txt 文档)
- **初始化**: `python knowledge/scripts/import_hardcoded_data.py`

## 📅 开发路线图

### ✅ 阶段 1: 基础框架（已完成）

- [x] 项目目录结构
- [x] 配置管理（复用config.py）
- [x] LLM客户端（LLM集成，支持 function calling）
- [x] **Skill 注册系统**（SkillRegistry）
- [x] **Agent Loop**（LLM 驱动的 Skill 调用循环）
- [x] **3个医疗 Skills**（知识库搜索、风险评估、症状分析）
- [x] BaseAgent（支持 Skill 注册和执行）
- [x] ConsultationAgent（支持 Skill 调用）
- [x] 主入口 main.py（交互式+单次查询）
- [x] 基础示例和测试

### ✅ 阶段 2: Agent Swarm 群体智能（已完成）

- [x] **SharedContext**（共享环境 - 类似蚁群信息素）
- [x] **Events**（事件驱动通信）
- [x] **LeadAgent**（任务分解和结果汇总）
- [x] **DiagnosticAgent**（症状诊断 Worker）
- [x] **ResearchAgent**（文献检索 Worker）
- [x] **AgentIdentity**（Agent 身份）
- [x] **SessionSummary**（会话总结和经验提取）
- [x] **SwarmCoordinator**（智能路由）
- [x] 自主任务认领机制
- [x] 并行执行支持
- [x] 完全向后兼容

### ✅ 阶段 3: 记忆系统增强（已完成）

- [x] Agent Identity Files（IDENTITY.md）
- [x] Session Summaries（会话总结）
- [x] **短期记忆**（会话级对话历史，支持内存/Redis）
- [x] **长期记忆**（Mem0云服务集成，向量相似度搜索）
- [x] 跨会话记忆检索
- [x] 相似案例推荐
- [x] 优雅降级（Mem0不可用时系统继续工作）

### ✅ 阶段 4: Skills 扩展（已完成）

- [x] **ConsultationAgent Skills 扩展**
  - [x] `recommend_lifestyle`: 饮食、运动、睡眠、用药的具体建议
  - [x] 建议数据库：高血压、糖尿病、感冒、冠心病、哮喘等常见疾病

- [x] **DiagnosticAgent Skills 扩展**
  - [x] `disease_code`: ICD-10编码查询和疾病分类
  - [x] ICD-10数据库：20+常见疾病编码和分类信息

- [x] **ResearchAgent Skills 扩展**
  - [x] `clinical_guideline`: 临床指南和专家共识检索
  - [x] 指南数据库：10+中华医学会权威指南

- [x] **测试和文档**
  - [x] Skills 单元测试（`examples/test_all.py`）
  - [x] 阶段 4测试套件（3个测试用例）

### ✅ 阶段 5: DeepResearch（已完成）

- [x] **网络搜索模块**（WebSearchTool）
  - [x] DuckDuckGo 搜索API集成
  - [x] 医学领域网站白名单
  - [x] 重试机制和错误处理
  - [x] 网页内容抓取和清洗

- [x] **本地知识库**（KnowledgeBase）
  - [x] Qdrant 向量数据库集成
  - [x] Sentence Transformers embedding（BAAI/bge-small-zh-v1.5，统一使用中文模型）
  - [x] 双模式支持（Qdrant / 内存存储）
  - [x] 向量相似度检索

- [x] **证据综合器**（EvidenceSynthesizer）
  - [x] 多来源信息整合
  - [x] LLM 驱动的证据综合
  - [x] 结构化报告生成（关键发现、证据等级、置信度）
  - [x] 信息冲突识别

- [x] **深度研究工作流**（DeepResearchWorkflow）
  - [x] 多步骤研究流程（查询规划 → 并行搜索 → 证据综合 → 质量验证）
  - [x] LLM 驱动的查询拆解
  - [x] 并行执行（asyncio.gather）
  - [x] 迭代式研究支持

- [x] **ResearchAgent 集成**
  - [x] 注册 `deep_research` 工具
  - [x] 更新 system prompt
  - [x] 自动判断是否需要 DeepResearch

- [x] **测试和文档完善**
  - [x] 核心组件单元测试（使用模拟数据）
  - [x] 端到端集成测试（18个测试用例全部通过）
  - [x] 集成到统一测试套件（examples/test_all.py）

### ✅ 阶段 6: 统一知识库架构（已完成）

- [x] **Milvus 向量数据库集成**
  - [x] Milvus Lite 本地部署
  - [x] BAAI/bge-small-zh-v1.5 中文 Embedding 模型
  - [x] 512维向量，COSINE 相似度

- [x] **数据迁移**
  - [x] 生活方式建议（5种疾病）
  - [x] ICD-10疾病编码（20+疾病）
  - [x] 临床指南（10+指南）
  - [x] 数据导入脚本（knowledge/scripts/import_hardcoded_data.py）

- [x] **Skills 重构**
  - [x] `search_knowledge` → Milvus 语义检索
  - [x] `recommend_lifestyle` → Milvus 检索生活方式建议
  - [x] `disease_code` → Milvus 检索 ICD-10 编码
  - [x] `clinical_guideline` → Milvus 检索临床指南

- [x] **语义检索能力**
  - [x] 模糊查询支持（"血压高" → "高血压"）
  - [x] 按类型过滤（lifestyle, disease_classification, clinical_guideline）
  - [x] 相似度评分

### ✅ 阶段 7: Harness Engineering 融合（已完成）

**核心理念**："人类设计约束，AI代理执行" —— 让 AI 在明确的约束下自主工作、自我修正。

#### Phase 1: 约束系统（Explicit Constraint System）

**目标**：将隐式的规则显式化为可验证的约束

- [x] **约束定义文件**
  - `constraints/agent_constraints.yaml`: 定义 Agent 能力边界、允许的工具、输出约束
  - `constraints/swarm_constraints.yaml`: 定义 Swarm 协作规则、任务分解规则、Agent 选择规则

- [x] **约束验证器**（`constraints/validator.py`）
  - `validate_tool_call()`: 验证工具调用是否允许
  - `validate_output()`: 验证输出是否符合约束（免责声明、高危警告等）
  - `validate_task_decomposition()`: 验证任务分解是否合理
  - `get_required_agents()`: 根据问题推荐必须包含的 Agent

- [x] **自动修复器**（`validation/auto_fixer.py`）
  - `fix_missing_disclaimer()`: 自动添加免责声明
  - `fix_high_risk_warning()`: 自动添加高危症状警告
  - `fix_output()`: 综合修复输出违规

- [x] **非侵入式集成**
  - 在 `core/agent_loop.py` 注入约束检查（工具调用前、输出生成后）
  - 不修改现有 Agent 代码，保持向后兼容
  - 优雅降级：约束模块不存在时系统继续工作

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

- 基于 [MediX-R1](https://github.com/...) 医学多模态模型
- 使用 [LLM API](https://www.volcengine.com/) 作为LLM后端
- 记忆管理基于 [Mem0](https://mem0.ai/)

---
