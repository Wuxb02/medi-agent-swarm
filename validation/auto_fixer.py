"""
自动修复器
根据约束违规自动修复输出

基于 Harness Engineering 原则：
- 自动检测问题
- 自动修复（在可能的情况下）
- 保持 Agent 输出质量
"""
from typing import Dict, Any, List
from loguru import logger


class AutoFixer:
    """自动修复器"""

    def fix_output(
        self,
        output: str,
        auto_fixable: List[str]
    ) -> str:
        """
        自动修复输出

        Args:
            output: 原始输出
            auto_fixable: 可修复的违规列表

        Returns:
            修复后的输出
        """
        fixed_output = output

        for fix_type in auto_fixable:
            if fix_type == "add_disclaimer":
                fixed_output = self.fix_missing_disclaimer(fixed_output)
            elif fix_type == "add_emergency_warning":
                fixed_output = self.fix_high_risk_warning(fixed_output)

        if fixed_output != output:
            logger.info("🔧 输出已自动修复")

        return fixed_output

    def fix_missing_disclaimer(self, output: str) -> str:
        """
        自动添加免责声明

        Args:
            output: 原始输出

        Returns:
            添加免责声明后的输出
        """
        if "免责" not in output and "仅供参考" not in output:
            disclaimer = "\n\n【免责声明】\n以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。"
            logger.debug("+ 自动添加免责声明")
            return output + disclaimer
        return output

    def fix_high_risk_warning(self, output: str) -> str:
        """
        自动添加高危症状警告

        Args:
            output: 原始输出

        Returns:
            添加警告后的输出
        """
        high_risk_keywords = ["胸痛", "呼吸困难", "昏厥", "剧烈头痛", "心悸", "突然视力模糊"]

        # 检查是否包含高危症状且未建议就医
        if any(kw in output for kw in high_risk_keywords):
            if "就医" not in output and "急诊" not in output and "医院" not in output:
                warning = "⚠️ **重要提醒**：您描述的症状可能提示严重问题，建议立即就医或拨打急救电话120，不要延误。\n\n"
                logger.debug("+ 自动添加高危症状警告")
                return warning + output

        return output

    def fix_excessive_length(self, output: str, max_length: int) -> str:
        """
        截断过长的输出

        Args:
            output: 原始输出
            max_length: 最大长度

        Returns:
            截断后的输出
        """
        if len(output) > max_length:
            logger.warning(f"输出过长（{len(output)} > {max_length}），自动截断")
            truncated = output[:max_length - 50]  # 保留50字空间添加提示
            truncated += "\n\n[回答内容较长，已截断。如需完整信息，请咨询专业医生]"
            return truncated

        return output

    def remove_diagnosis_statements(self, output: str) -> str:
        """
        移除明确的诊断语句（高级功能，需要 LLM 辅助）

        Args:
            output: 原始输出

        Returns:
            移除诊断语句后的输出
        """
        # 简单替换（实际应该使用 LLM 进行更智能的重写）
        output = output.replace("您患有", "可能存在")
        output = output.replace("确诊为", "建议检查")
        output = output.replace("肯定是", "很可能是")

        return output
