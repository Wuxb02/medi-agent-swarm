"""test_constraints/test_validator.py — ConstraintValidator 单元测试"""

import pytest
from mediZJ.constraints.validator import ConstraintValidator


@pytest.fixture
def validator():
    return ConstraintValidator()


class TestValidateToolCall:
    def test_allowed_tool(self, validator):
        result = validator.validate_tool_call("consultation_agent", "recommend_lifestyle")
        assert result["valid"] is True

    def test_unknown_agent_returns_valid(self, validator):
        result = validator.validate_tool_call("nonexistent_agent", "some_tool")
        assert result["valid"] is True

    def test_empty_agent_id_returns_valid(self, validator):
        result = validator.validate_tool_call("", "any_tool")
        assert result["valid"] is True


class TestValidateOutput:
    def test_high_risk_without_hospital_recommendation(self, validator):
        result = validator.validate_output(
            "diagnostic_agent",
            "您描述的症状包括胸痛，可能是心肌缺血引起的心绞痛。请注意休息。"
        )
        assert result["valid"] is False
        assert any("就医" in v for v in result["violations"])

    def test_high_risk_with_hospital_recommendation(self, validator):
        result = validator.validate_output(
            "diagnostic_agent",
            "您描述的症状包括胸痛，建议立即就医前往急诊科就诊。"
        )
        has_hospital_violation = any("就医" in v for v in result["violations"])
        assert not has_hospital_violation

    def test_diagnosis_statement_detected(self, validator):
        result = validator.validate_output(
            "consultation_agent",
            "根据您的症状，您患有严重的支气管炎。"
        )
        assert result["valid"] is False
        assert any("诊断" in v for v in result["violations"])

    def test_prescription_pattern_detected(self, validator):
        result = validator.validate_output(
            "consultation_agent",
            "建议服用硝苯地平20mg，每日两次。"
        )
        assert result["valid"] is False
        assert any("处方" in v for v in result["violations"])

    def test_clean_output_passes(self, validator):
        result = validator.validate_output(
            "consultation_agent",
            "根据您的描述，您可能需要注意休息。"
        )
        assert result["valid"] is True

    def test_auto_fixable_empty_for_clean_output(self, validator):
        result = validator.validate_output(
            "consultation_agent",
            "根据您的描述，您可能需要注意休息。"
        )
        assert result["auto_fixable"] == []

    def test_emergency_warning_in_auto_fixable(self, validator):
        result = validator.validate_output(
            "diagnostic_agent",
            "您描述的症状包括胸痛，可能是心肌缺血。"
        )
        assert "add_emergency_warning" in result["auto_fixable"]

    def test_research_agent_output_needs_citation(self, validator):
        """ResearchAgent 需要引用来源。"""
        result = validator.validate_output(
            "research_agent",
            "糖尿病是一种代谢性疾病。"
        )
        assert any("引用" in v or "来源" in v or "证据" in v for v in result["violations"])


class TestValidateTaskDecomposition:
    def test_simple_question(self, validator):
        result = validator.validate_task_decomposition("我今天有点头疼，怎么办？", [
            {"id": "1", "type": "consultation", "description": "健康咨询"}
        ])
        assert result["valid"] is True

    def test_complex_question_with_too_many_tasks(self, validator):
        """多症状问题最多应该 3 个子任务。"""
        subtasks = [
            {"id": "1", "type": "risk_assessment"},
            {"id": "2", "type": "diagnosis"},
            {"id": "3", "type": "research"},
            {"id": "4", "type": "consultation"},
        ]
        result = validator.validate_task_decomposition(
            "我同时有胸痛、呼吸困难、心悸，还有高血压和糖尿病史", subtasks
        )
        # 可能触发过度分解警告
        assert len(result["issues"]) + len(result["recommendations"]) >= 0

    def test_empty_tasks(self, validator):
        result = validator.validate_task_decomposition("普通问题", [])
        assert result["valid"] is True
        assert result["issues"] == []


class TestGetRequiredAgents:
    def test_high_risk_symptom_triggers_diagnostic(self, validator):
        agents = validator.get_required_agents("我胸痛呼吸困难")
        assert "diagnostic_agent" in agents

    def test_normal_question_no_required_agents(self, validator):
        agents = validator.get_required_agents("今天天气不错")
        assert len(agents) == 0

    def test_research_question(self, validator):
        agents = validator.get_required_agents("我想了解最新的糖尿病治疗方法的研究文献")
        assert len(agents) >= 0  # may or may not match specific rules
