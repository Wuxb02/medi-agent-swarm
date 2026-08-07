"""test_core/test_skill_registry.py — SkillParameter 数据模型 单元测试

LangGraph 迁移后 SkillRegistry 类已删除，此处仅保留 SkillParameter 数据类测试。
"""

from mediZJ.core.skill_registry import SkillParameter


class TestSkillParameter:
    def test_basic_parameter(self):
        p = SkillParameter(name="query", type="string", description="搜索词", required=True)
        assert p.name == "query"
        assert p.type == "string"
        assert p.description == "搜索词"
        assert p.required is True

    def test_parameter_with_enum(self):
        p = SkillParameter(
            name="type", type="string", description="类型",
            enum=["a", "b", "c"],
        )
        assert p.enum == ["a", "b", "c"]

    def test_parameter_defaults(self):
        p = SkillParameter(name="q", type="string", description="query")
        assert p.required is False
        assert p.enum is None

    def test_parameter_mutable_enum(self):
        p = SkillParameter(name="mode", type="string", description="mode", enum=["x"])
        p.enum.append("y")
        assert p.enum == ["x", "y"]
