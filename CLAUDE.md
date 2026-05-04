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

# 运行应用
python main.py          # 交互模式
python main.py -v       # 详细日志模式

# 运行测试（26+ 测试用例，8 个阶段）
python examples/test_all.py
```

## 架构设计

### 请求处理流程

```
用户输入 → main.py → SwarmCoordinator
  ├─ 简单问题 → 单个 Agent 直接处理
  └─ 复杂问题 → LeadAgent 任务分解
                  ├─ ConsultationAgent (健康咨询)
                  ├─ DiagnosticAgent (症状诊断)
                  └─ ResearchAgent (医学研究)
                  → LeadAgent 结果综合 → 最终回答
```

每个 Worker Agent 内部运行 `AgentLoop`（Think-Act-Observe 循环），每次最多执行 2 次工具调用。

### 核心模块

| 模块 | 职责 |
|------|------|
| `core/llm_client.py` | OpenAI 兼容的异步 LLM 客户端 |
| `core/agent_loop.py` | 核心执行引擎：Think-Act-Observe 循环，集成约束验证、自动修复和动态工具刷新 |
| `core/skill_registry.py` | 双层注册：Skill（能力包）+ Tool（底层函数），兼容模式自动检测 |
| `core/skill_loader.py` | 从 `.claude/skills/` 动态发现技能，提取 SKILL.md 正文作为指令 |
| `core/skill_models.py` | SkillDefinition 数据模型 |
| `core/base_tools.py` | 基础工具工厂（activate_skill） |
| `agents/base_agent.py` | Agent 抽象基类 |
| `swarm/swarm_coordinator.py` | 顶层协调器，路由决策 + 并行调度（90s 超时） |
| `swarm/lead_agent.py` | 任务分解 + 结果综合 |
| `swarm/shared_context.py` | 共享黑板系统（SubTask/Contribution 生命周期管理） |
| `memory/short_term.py` | 会话级短期记忆（支持内存/Redis） |
| `memory/long_term.py` | 跨会话长期记忆（Mem0 云服务） |
| `memory/entropy_manager.py` | 记忆去重、压缩、熵估计 |
| `knowledge/milvus_kb.py` | Milvus Lite 向量知识库（BAAI/bge-small-zh-v1.5 嵌入模型） |
| `research/deep_research_workflow.py` | 多步骤研究流水线 |
| `constraints/validator.py` | 运行时约束验证（工具权限、输出质量） |
| `validation/` | 自动修复违规输出（添加免责声明、警告等） |

### 关键设计模式

- **单例模式**：`MedicalKnowledgeBase`、`ShortTermMemory`
- **Mixin 模式**：`SkillRegistryMixin` 为所有 Worker Agent 提供共享的技能注册
- **共享黑板**：`SharedContext` 作为去中心化通信介质
- **Harness Engineering**：非侵入式约束验证 + 自动修复注入 AgentLoop

### Skills（9 个）— Skill + Tool 双层架构

位于 `.claude/skills/` 下，每个 Skill 是一个能力包：

- `SKILL.md`：YAML frontmatter（name、description、tools 声明）+ Markdown 正文（Skill 指令，激活时注入 system prompt）
- `script/`：Python 实现（工具函数）

调用流程：LLM 从 system prompt 看到所有 Skill 描述 → `activate_skill("name")` → 指令注入 + 工具加载 → 执行任务

`search-knowledge`、`assess-risk`、`analyze-symptoms`、`recommend-lifestyle`、`disease-code`、`clinical-guideline`、`deep-research`、`search-history`、`search-similar-cases`

## 配置

环境变量（`.env`）：
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_NAME` — OpenAI 兼容 LLM 配置
- `LLM_TEMPERATURE`（默认 0.7）、`LLM_MAX_TOKENS`（默认 8192）
- `MEM0_API_KEY` — 可选，Mem0 长期记忆服务

约束定义（YAML）：
- `constraints/agent_constraints.yaml` — 各 Agent 能力边界、允许工具、禁止行为
- `constraints/swarm_constraints.yaml` — Swarm 协作规则、任务分解策略

## 已知问题

- `validation/__init__.py` 导入 `from .auto_fixer`，但实际文件名为 `auto_fixer_20260428_231043.py`，会导致 ImportError（Harness 功能优雅降级）
- `swarm/__init__.py` 导入 `from .events`，但实际文件名为 `events_20260428_231035.py`，会导致事件系统不可用
- 测试使用 raw assert 而非 pytest/unittest，无正式测试框架
- 无 linting/formatting/CI 配置
