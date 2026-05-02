"""
约束验证器
运行时检查 Agent 行为是否违反约束

基于 Harness Engineering 原则：
- 显式化约束
- 运行时验证
- 自动修复（可选）
"""
from typing import Dict, Any, List, Optional
import yaml
from pathlib import Path
from loguru import logger


class ConstraintValidator:
    """约束验证器"""

    def __init__(
        self,
        agent_constraints_file: str = "constraints/agent_constraints.yaml",
        swarm_constraints_file: str = "constraints/swarm_constraints.yaml"
    ):
        """
        初始化约束验证器

        Args:
            agent_constraints_file: Agent约束定义文件
            swarm_constraints_file: Swarm约束定义文件
        """
        # 加载 Agent 约束
        agent_path = Path(__file__).parent / "agent_constraints.yaml"
        with open(agent_path, 'r', encoding='utf-8') as f:
            self.agent_constraints = yaml.safe_load(f)

        # 加载 Swarm 约束
        swarm_path = Path(__file__).parent / "swarm_constraints.yaml"
        with open(swarm_path, 'r', encoding='utf-8') as f:
            self.swarm_constraints = yaml.safe_load(f)

        logger.info("✅ ConstraintValidator initialized")

    def validate_tool_call(self, agent_id: str, tool_name: str) -> Dict[str, Any]:
        """
        验证工具调用是否允许

        Args:
            agent_id: Agent ID
            tool_name: 工具名称

        Returns:
            {
                "valid": bool,
                "reason": str (如果不允许)
            }
        """
        agent_constraints = self.agent_constraints['agents'].get(agent_id, {})
        allowed_tools = agent_constraints.get('allowed_tools', [])

        # 如果 allowed_tools 为空，表示没有限制
        if not allowed_tools:
            return {"valid": True}

        # 检查工具是否在允许列表中
        if tool_name not in allowed_tools:
            reason = f"工具 {tool_name} 不在 {agent_id} 的推荐工具列表中"
            logger.warning(f"⚠️ {reason}")
            return {
                "valid": False,
                "reason": reason,
                "severity": "warning"  # 警告级别（不阻止执行，只记录）
            }

        return {"valid": True}

    def validate_output(self, agent_id: str, output: str) -> Dict[str, Any]:
        """
        验证输出是否符合约束

        Args:
            agent_id: Agent ID
            output: Agent 的输出文本

        Returns:
            {
                "valid": bool,
                "violations": List[str],
                "auto_fixable": List[str]  # 可以自动修复的违规
            }
        """
        agent_constraints = self.agent_constraints['agents'].get(agent_id, {})
        output_constraints = agent_constraints.get('output_constraints', [])
        common_constraints = self.agent_constraints.get('common', {}).get('output_constraints', [])

        # 合并约束
        all_constraints = output_constraints + common_constraints

        violations = []
        auto_fixable = []

        # 检查免责声明
        if 'must_include_disclaimer' in all_constraints:
            if '免责声明' not in output and 'disclaimer' not in output.lower() and '仅供参考' not in output:
                violations.append("缺少免责声明")
                auto_fixable.append("add_disclaimer")

        # 检查长度限制
        max_length_constraint = next(
            (c for c in all_constraints if isinstance(c, dict) and 'max_response_length' in c),
            None
        )
        if max_length_constraint:
            max_length = max_length_constraint.get('max_response_length')
            if len(output) > max_length:
                violations.append(f"回答过长（{len(output)} > {max_length}字）")

        # 检查高危症状必须建议就医
        if 'must_recommend_doctor_visit_if_high_risk' in all_constraints:
            high_risk_keywords = ["胸痛", "呼吸困难", "昏厥", "剧烈头痛", "心悸", "突然视力模糊"]
            if any(kw in output for kw in high_risk_keywords):
                if "就医" not in output and "急诊" not in output and "医院" not in output:
                    violations.append("高危症状未建议就医")
                    auto_fixable.append("add_emergency_warning")

        # 检查是否引用来源（仅 ResearchAgent）
        if 'must_cite_sources' in all_constraints:
            if "指南" not in output and "文献" not in output and "研究" not in output:
                violations.append("未引用来源或证据")

        # 检查禁止行为
        forbidden_actions = agent_constraints.get('forbidden_actions', [])
        if 'diagnose_disease' in forbidden_actions:
            if any(phrase in output for phrase in ["您患有", "确诊为", "肯定是", "就是"]):
                violations.append("包含明确诊断（越界行为）")

        if 'prescribe_medication' in forbidden_actions:
            # 更精细的药物处方检测（避免误报）
            # 只检测明确的药物处方模式
            import re

            # 模式1: 具体药物剂量（如：硝苯地平20mg）
            if re.search(r'(药物|药品|药).{0,10}(\d+\s*(mg|g|毫克|克))', output):
                violations.append("包含具体药物处方（越界行为）")

            # 模式2: 用药频率和剂量（如：每日3次，每次10mg）
            elif re.search(r'每(日|天|次).{0,5}\d+\s*次.{0,10}(\d+\s*(mg|g|毫克|克))', output):
                violations.append("包含具体药物处方（越界行为）")

            # 模式3: 明确的处方建议（如：建议服用XX 20mg）
            elif re.search(r'(建议|推荐)(服用|使用).{0,15}\d+\s*(mg|g|毫克|克)', output):
                violations.append("包含具体药物处方（越界行为）")

        return {
            "valid": len(violations) == 0,
            "violations": violations,
            "auto_fixable": auto_fixable
        }

    def validate_task_decomposition(
        self,
        question: str,
        subtasks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        验证任务分解是否合理（基于 Swarm 约束）

        Args:
            question: 用户问题
            subtasks: LeadAgent 分解的子任务列表

        Returns:
            {
                "valid": bool,
                "issues": List[str],
                "recommendations": List[str]
            }
        """
        rules = self.swarm_constraints['swarm']['task_decomposition_rules']
        issues = []
        recommendations = []

        num_subtasks = len(subtasks)

        # 检查是否匹配规则
        for rule in rules:
            pattern = rule['pattern']
            keywords = pattern.split('|')

            if any(kw in question for kw in keywords):
                max_subtasks = rule.get('max_subtasks')
                min_subtasks = rule.get('min_subtasks', 1)

                if max_subtasks and num_subtasks > max_subtasks:
                    issues.append(
                        f"任务过度分解：{rule['name']} 类型问题最多 {max_subtasks} 个子任务，"
                        f"当前 {num_subtasks} 个"
                    )
                    recommendations.append(f"建议合并为 {max_subtasks} 个任务")

                if min_subtasks and num_subtasks < min_subtasks:
                    issues.append(
                        f"任务分解不足：{rule['name']} 类型问题至少需要 {min_subtasks} 个子任务，"
                        f"当前 {num_subtasks} 个"
                    )

                # 找到匹配规则，停止检查
                break

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "recommendations": recommendations
        }

    def get_required_agents(self, question: str) -> List[str]:
        """
        根据约束规则推荐必须包含的 Agent

        Args:
            question: 用户问题

        Returns:
            必须包含的 Agent ID 列表
        """
        rules = self.swarm_constraints['swarm']['agent_selection_rules']
        required_agents = []

        for rule in rules:
            # 检查症状关键词
            if_symptoms = rule.get('if_symptoms', [])
            if any(symptom in question for symptom in if_symptoms):
                required_agents.extend(rule['must_include'])
                logger.info(
                    f"🔒 检测到高危症状，必须包含: {rule['must_include']}"
                    f"（{rule['reason']}）"
                )

            # 检查一般关键词
            if_keywords = rule.get('if_keywords', [])
            if any(kw in question for kw in if_keywords):
                required_agents.extend(rule['must_include'])
                logger.info(
                    f"💡 检测到关键词，推荐包含: {rule['must_include']}"
                    f"（{rule['reason']}）"
                )

        return list(set(required_agents))  # 去重
