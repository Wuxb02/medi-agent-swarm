"""
LeadAgent：任务分解和结果汇总

注意：LeadAgent 不是编排器！
- 只负责分解任务和汇总结果
- 不控制 Worker 的执行顺序
- 不直接调用 Worker
- Worker 自主认领任务并执行
"""
import uuid
from typing import Dict, Any, List, Optional
from loguru import logger

from core.llm_client import LLMClient
from .shared_context import SharedContext, SubTask, TaskStatus
from .events import Event, EventType


class LeadAgent:
    """
    Lead Agent：任务协调者

    职责：
    1. 评估问题复杂度
    2. 分解复杂任务为独立子任务
    3. 等待 Worker 完成
    4. 汇总所有结果

    不做：
    - 不编排执行顺序
    - 不分配任务给特定 Agent
    - 不控制 Worker 行为
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.agent_id = "lead_agent"
        self.llm_client = llm_client or LLMClient()

    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return """你是医疗 Swarm 的 Lead Agent。你的职责是**分析问题并分配给合适的 Worker Agent**。

**核心原则**：
1. **尽量少分配任务**：能用 1 个 Agent 解决的，不要用 2 个；能用 2 个的，不要用 3 个
2. **优先使用 ConsultationAgent**：对于常见病症（感冒、发烧、咳嗽等）、健康科普，单独使用 ConsultationAgent 就足够
3. 你**只负责分配 Agent**，不决定具体使用哪些工具/技能（Worker Agent 会自己选择）
4. 子任务应该相对独立，可以并行执行

---

## 可用的 Worker Agents

### 1. ConsultationAgent（健康咨询专家）
**擅长**：
- 常见疾病科普和健康建议
- 症状初步评估和风险分级
- 生活方式指导（饮食、运动、睡眠）
- 日常健康管理

**适用场景**：
- 简单症状咨询（"我感冒了""头痛怎么办"）
- 健康科普（"什么是高血压""多喝水的好处"）
- 预防建议（"如何预防感冒"）
- 生活方式指导（"高血压患者饮食注意什么"）

---

### 2. DiagnosticAgent（诊断推理专家）
**擅长**：
- 症状模式分析和关联性评估
- 鉴别诊断推理
- 疾病编码和分类（ICD-10）
- 复杂症状的风险评估

**适用场景**：
- 复杂症状分析（"头痛+恶心+视力模糊"）
- 多系统问题（"胸闷气短冒冷汗，严重吗"）
- 症状持续加重（"头痛一周了越来越严重"）
- 需要鉴别诊断的情况

---

### 3. ResearchAgent（循证医学专家）
**擅长**：
- 临床指南和诊疗规范检索
- 最新医学研究和证据综合
- 权威治疗方案查询
- 文献支持和证据等级评估

**适用场景**：
- 需要权威指南（"高血压最新诊疗指南"）
- 询问标准治疗方案（"糖尿病如何治疗"）
- 需要最新医学进展
- 需要循证医学证据支持

---

## 任务分配策略

### 策略 1：简单问题 → 1 个 Agent（ConsultationAgent）
**问题特征**：
- 单一常见症状（感冒、发烧、头痛、咳嗽）
- 健康科普和预防建议
- 一般性健康咨询

**示例**：
- "我感冒了怎么办？" → ConsultationAgent
- "什么是高血压？" → ConsultationAgent
- "如何预防流感？" → ConsultationAgent
- "糖尿病患者饮食注意什么？" → ConsultationAgent

---

### 策略 2：复杂症状 → 2 个 Agents（DiagnosticAgent + ConsultationAgent）
**问题特征**：
- 多个症状组合（3个以上不同症状）
- 症状持续时间长或加重
- 明确询问严重程度或是否需要就医
- 有既往病史或用药史

**示例**：
- "头痛一周了越来越严重，需要就医吗？" → DiagnosticAgent (评估风险) + ConsultationAgent (处理建议)
- "胸闷气短冒冷汗，严重吗？" → DiagnosticAgent (症状分析) + ConsultationAgent (建议)

---

### 策略 3：需要权威指南 → 2-3 个 Agents
**问题特征**：
- 询问疾病治疗方案
- 需要标准诊疗规范
- 需要权威指南和生活建议的综合方案

**示例**：
- "高血压如何治疗？" → ResearchAgent (指南) + ConsultationAgent (生活建议)
- "糖尿病最新诊疗指南是什么？" → ResearchAgent

---

## 输出格式（JSON）

**重要**：输出中**不需要 `type` 字段**，只需要 `description`（任务描述）和 `assigned_agent`（分配的 Agent）

### 示例 1：简单问题（1 个 Agent）
```json
{
  "subtasks": [
    {
      "description": "回答用户关于感冒的咨询，提供处理建议和注意事项",
      "assigned_agent": "consultation_agent"
    }
  ]
}
```

### 示例 2：复杂症状（2 个 Agents）
```json
{
  "subtasks": [
    {
      "description": "评估头痛症状的风险等级、紧急程度，分析症状模式和可能原因",
      "assigned_agent": "diagnostic_agent"
    },
    {
      "description": "提供头痛的处理建议、缓解方法和注意事项",
      "assigned_agent": "consultation_agent"
    }
  ]
}
```

### 示例 3：需要指南（2 个 Agents）
```json
{
  "subtasks": [
    {
      "description": "检索高血压的最新临床诊疗指南和标准治疗方案",
      "assigned_agent": "research_agent"
    },
    {
      "description": "提供高血压患者的日常生活管理建议（饮食、运动、用药）",
      "assigned_agent": "consultation_agent"
    }
  ]
}
```

---

## 关键要点

1. **只写 `description` 和 `assigned_agent`**
2. **description 要具体**：明确说明这个 Agent 需要做什么
3. **Agent 会自主选择工具**：你不需要指定使用哪个工具/技能
4. **尽量少分配**：1 个 Agent 能搞定的，不要分配 2 个
5. **任务要独立**：各个 Agent 的任务应该可以并行执行
"""

    async def assess_and_decompose(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        分析问题并分解为子任务

        返回：
        - subtasks: List[SubTask] - 子任务列表
          每个子任务包含：type（工具名）、description（描述）、assigned_agent（负责的Agent）
        """
        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": f"问题：{question}\n\n背景：{context or '无'}"}
        ]

        try:
            content = await self.llm_client.chat(messages)

            logger.debug(f"LeadAgent assessment: {content[:200]}...")

            import json
            import re

            # 尝试提取 JSON
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return result

            # 回退：假设是简单问题，交给 ConsultationAgent
            return {
                "subtasks": [{
                    "type": "knowledge_search",
                    "description": "回答用户问题",
                    "assigned_agent": "consultation_agent"
                }],
                "reason": "无法解析 LLM 响应，默认使用 ConsultationAgent"
            }

        except Exception as e:
            logger.error(f"LeadAgent assessment error: {e}")
            return {
                "subtasks": [],
                "reason": f"评估失败：{e}"
            }

    def create_subtasks(
        self,
        decomposition_result: Dict[str, Any],
        shared_context: SharedContext
    ) -> List[SubTask]:
        """
        根据分解结果创建 SubTask 并发布到 SharedContext

        直接指定 assigned_agent（中心化分配）
        """
        subtasks_data = decomposition_result.get("subtasks", [])
        subtasks = []

        for data in subtasks_data:
            # 自动推断 type（基于 assigned_agent，向后兼容）
            # LeadAgent 不再输出 type 字段，这里根据 Agent 生成通用 type
            assigned_agent = data["assigned_agent"]
            inferred_type = data.get("type") or f"{assigned_agent}_task"

            subtask = SubTask(
                id=str(uuid.uuid4()),
                type=inferred_type,
                description=data["description"],
                assigned_agent=assigned_agent
            )

            shared_context.add_subtask(subtask)
            subtasks.append(subtask)

            logger.info(
                f"Created SubTask: {subtask.type} "
                f"(assigned to: {subtask.assigned_agent})"
            )

        return subtasks

    async def wait_for_completion(
        self,
        shared_context: SharedContext,
        timeout: float = 30.0
    ) -> bool:
        """
        等待所有子任务完成

        这不是"主动控制"，而是"被动等待"
        Worker 自主完成任务，Lead 只是等待
        """
        import asyncio

        start_time = asyncio.get_event_loop().time()

        while True:
            if shared_context.is_all_subtasks_completed():
                logger.info("All subtasks completed")
                return True

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                logger.warning(f"Timeout waiting for subtasks ({timeout}s)")
                return False

            await asyncio.sleep(0.5)  # 每 0.5 秒检查一次

    async def synthesize_results(
        self,
        question: str,
        shared_context: SharedContext,
        timeout_occurred: bool = False
    ) -> str:
        """
        汇总所有 Agent 的贡献，生成最终答案

        这是 Lead Agent 的核心价值：整合多个视角

        Args:
            question: 用户问题
            shared_context: 共享上下文
            timeout_occurred: 是否发生超时
        """
        # 收集所有贡献
        all_contributions = shared_context.get_contributions()

        if not all_contributions:
            # 如果没有任何贡献
            if timeout_occurred:
                return """抱歉，由于系统响应超时，所有 Agent 均未能在规定时间内完成分析。

【建议】：
- 您的问题可能比较复杂，建议简化问题后重试
- 或者将问题拆分为多个小问题分别咨询

【紧急情况】：
如果您的症状严重或紧急，请立即就医或拨打急救电话，不要依赖在线咨询。"""
            else:
                return "抱歉，Swarm 未能提供有效分析结果。"

        # 构建汇总提示
        contributions_text = []
        completed_agents = []
        for contrib in all_contributions:
            subtask = shared_context.get_subtask(contrib.subtask_id)
            contributions_text.append(
                f"**{contrib.agent_id}** ({subtask.type if subtask else '未知'}):\n"
                f"{contrib.result}"
            )
            completed_agents.append(contrib.agent_id)

        # 如果发生超时，添加说明
        timeout_note = ""
        if timeout_occurred:
            all_subtasks = shared_context.task_decomposition.values()
            incomplete_tasks = [
                subtask.type for subtask in all_subtasks
                if subtask.status.value in ["pending", "claimed"]
            ]
            if incomplete_tasks:
                timeout_note = f"""

**注意**：由于系统响应超时，以下分析模块未能完成：{', '.join(incomplete_tasks)}
以下是基于已完成的 {len(completed_agents)} 个 Agent 的部分分析结果。"""

        synthesis_prompt = f"""你是医疗 Swarm 的 Lead Agent，负责汇总多个专业 Agent 的分析结果。

**用户问题**：{question}

**Agent 贡献**：
{chr(10).join(contributions_text)}{timeout_note}

**任务**：
整合以上所有分析，生成一个全面、专业的最终答案。

**要求**：
1. 综合所有 Agent 的观点
2. 突出多角度分析的优势
3. 保持医疗建议的严谨性
4. 包含【风险评估】【诊断分析】【医学证据】等模块（如果相关 Agent 提供了）
5. 给出【核心建议】
6. 添加【免责声明】
{"7. 如果有分析模块未完成，在答案中明确说明" if timeout_occurred else ""}

**输出格式**：
【风险评估】 (如果有)
...

【诊断分析】 (如果有)
...

【医学证据】 (如果有)
...

【核心建议】
1. ...
2. ...

【免责声明】
...
"""

        try:
            response = await self.llm_client.chat([
                {"role": "user", "content": synthesis_prompt}
            ])

            return response

        except Exception as e:
            logger.error(f"Synthesis error: {e}")
            return f"汇总结果时出错：{e}"
