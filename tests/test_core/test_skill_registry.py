"""test_core/test_skill_registry.py — SkillRegistry 注册/激活/执行 单元测试"""

import pytest
from unittest.mock import MagicMock, patch
from mediZJ.core.skill_registry import SkillRegistry, SkillParameter


class TestSkillParameter:
    def test_basic_parameter(self):
        p = SkillParameter(name="query", type="string", description="搜索词", required=True)
        assert p.name == "query"
        assert p.type == "string"
        assert p.required is True

    def test_parameter_with_enum(self):
        p = SkillParameter(
            name="type", type="string", description="类型",
            enum=["a", "b", "c"],
        )
        assert p.enum == ["a", "b", "c"]


class TestRegistryInit:
    def test_default_state(self):
        reg = SkillRegistry()
        assert reg.compat_mode is True
        assert reg.active_skill is None
        assert reg.base_tools == {}
        assert reg.skills == {}

    def test_set_compat_mode(self):
        reg = SkillRegistry()
        reg.set_compat_mode(False)
        assert reg.compat_mode is False


class TestLegacyRegister:
    @pytest.fixture
    def reg(self):
        return SkillRegistry()

    def test_register_and_get(self, reg):
        def my_func(q):
            """test function"""
            return {"result": q}
        reg.register("my_skill", my_func, "a test skill", [])
        skill = reg.get("my_skill")
        assert skill is not None
        assert skill["function"] is my_func

    def test_get_all(self, reg):
        def f1(): return {}
        def f2(): return {}
        reg.register("s1", f1, "d1", [])
        reg.register("s2", f2, "d2", [])
        assert len(reg.get_all()) == 2

    @pytest.mark.asyncio
    async def test_execute_legacy_skill(self, reg):
        def my_func(q: str):
            return {"result": q}
        reg.register("search", my_func, "search tool", [
            SkillParameter(name="q", type="string", description="query", required=True)
        ])
        result = await reg.execute("search", q="test")
        assert result == {"result": "test"}

    @pytest.mark.asyncio
    async def test_execute_nonexistent_tool(self, reg):
        result = await reg.execute("no-such-tool")
        assert result["success"] is False
        assert "not found" in result["error"]


class TestBaseTools:
    @pytest.fixture
    def reg(self):
        r = SkillRegistry()
        r.set_compat_mode(False)
        return r

    def test_register_base_tool(self, reg):
        def base_func():
            return {"ok": True}
        reg.register_base_tool("base_tool", base_func, "base tool desc", [])
        assert "base_tool" in reg.base_tools

    @pytest.mark.asyncio
    async def test_execute_base_tool(self, reg):
        def base_func(name: str):
            return {"hello": name}
        reg.register_base_tool("greet", base_func, "greeting", [
            SkillParameter(name="name", type="string", description="name", required=True)
        ])
        result = await reg.execute("greet", name="world")
        assert result == {"hello": "world"}

    @pytest.mark.asyncio
    async def test_base_tool_preferred_over_legacy(self, reg):
        reg.set_compat_mode(True)
        def base_func():
            return {"from": "base"}
        def legacy_func():
            return {"from": "legacy"}
        reg.register_base_tool("shared_name", base_func, "base", [])
        reg.register("shared_name", legacy_func, "legacy", [])
        result = await reg.execute("shared_name")
        assert result == {"from": "base"}


class TestToOpenAIFormat:
    def test_compat_mode_format(self):
        reg = SkillRegistry()
        def f1(q): return {}
        reg.register("tool1", f1, "desc1", [
            SkillParameter(name="q", type="string", description="query", required=True)
        ])
        schemas = reg.to_openai_format()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "tool1"

    def test_empty_registry(self):
        reg = SkillRegistry()
        assert reg.to_openai_format() == []


class TestSkillDefinitionRegistration:
    @pytest.fixture
    def reg(self):
        r = SkillRegistry()
        r.set_compat_mode(False)
        return r

    def test_register_skill(self, reg):
        from mediZJ.core.skill_models import SkillDefinition
        sd = SkillDefinition(
            name="test_skill",
            description="test skill",
            instructions="do something",
            tool_names=["tool_a"],
            tool_functions={"tool_a": lambda: {}},
            tool_parameters={"tool_a": []},
            migrated=True,
        )
        reg.register_skill(sd)
        assert reg.get_skill_definition("test_skill") is sd
        assert reg.has_migrated_skills() is True

    def test_activate_skill(self, reg):
        from mediZJ.core.skill_models import SkillDefinition
        sd = SkillDefinition(
            name="test_skill",
            description="test skill",
            instructions="do something",
            tool_names=[],
            tool_functions={},
            tool_parameters={},
            migrated=True,
        )
        reg.register_skill(sd)
        name, instructions = reg.activate_skill("test_skill")
        assert name == "test_skill"
        assert instructions == "do something"
        assert reg.get_active_skill_name() == "test_skill"
        assert reg.get_active_instructions() == "do something"

    def test_activate_nonexistent_skill(self, reg):
        name, instructions = reg.activate_skill("no-such-skill")
        assert name is None
        assert instructions is None

    def test_get_skills_catalog(self, reg):
        from mediZJ.core.skill_models import SkillDefinition
        sd = SkillDefinition(
            name="test_skill", description="a test skill", instructions="",
            tool_names=["t1"], tool_functions={}, tool_parameters={}, migrated=True,
        )
        reg.register_skill(sd)
        catalog = reg.get_skills_catalog()
        assert "test_skill" in catalog
        assert "1" in catalog  # tool_count=1 在格式字符串中出现

    def test_get_skills_catalog_empty(self, reg):
        assert reg.get_skills_catalog() == "（无可用 Skills）"
