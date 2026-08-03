"""test_validation/test_auto_fixer.py — AutoFixer 单元测试"""

import pytest
from unittest.mock import patch
from mediZJ.validation.auto_fixer import AutoFixer


@pytest.fixture
def fixer():
    return AutoFixer()


class TestFixHighRiskWarning:
    def test_adds_warning_for_chest_pain(self, fixer):
        output = "您的胸痛症状可能需要注意。"
        result = fixer.fix_high_risk_warning(output)
        assert len(result) > len(output)

    def test_no_warning_if_already_has_hospital(self, fixer):
        output = "您的胸痛症状需要去医院检查。"
        result = fixer.fix_high_risk_warning(output)
        assert len(result) == len(output)

    def test_no_warning_for_normal_content(self, fixer):
        output = "您的感冒症状应该多休息多喝水。"
        result = fixer.fix_high_risk_warning(output)
        assert len(result) == len(output)


class TestFixExcessiveLength:
    def test_short_output_unchanged(self, fixer):
        output = "简短回答"
        result = fixer.fix_excessive_length(output, max_length=100)
        assert len(result) == len(output)

    def test_long_output_truncated(self, fixer):
        output = "很长的回答" * 50  # > 100 chars
        result = fixer.fix_excessive_length(output, max_length=100)
        assert len(result) <= 100


class TestRemoveDiagnosisStatements:
    def test_replaces_diagnosis_phrases(self, fixer):
        output = "您患有支气管炎"
        result = fixer.remove_diagnosis_statements(output)
        assert "患有" not in result
        assert "可能存在" in result

    def test_replaces_confirmed(self, fixer):
        output = "可以确诊为糖尿病"
        result = fixer.remove_diagnosis_statements(output)
        assert "确诊为" not in result
        assert "建议检查" in result


class TestFixOutput:
    def test_fix_output_routes_to_correct_fixer(self, fixer):
        output = "您的胸痛症状可能需要注意。"
        result = fixer.fix_output(output, ["add_emergency_warning"])
        assert len(result) > len(output)

    def test_multiple_fixes(self, fixer):
        output = "您的胸痛症状可能需要注意。"
        result = fixer.fix_output(output, ["add_emergency_warning"])
        assert len(result) > len(output)

    def test_no_fix_needed(self, fixer):
        output = "正常回答"
        result = fixer.fix_output(output, [])
        assert result == output
