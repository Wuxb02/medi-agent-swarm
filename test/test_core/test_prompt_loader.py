"""test_core/test_prompt_loader.py — PromptLoader 单元测试"""

import pytest
from core.prompt_loader import PromptLoader


class TestPromptLoader:
    def test_exists_valid_template(self):
        assert PromptLoader.exists("agents/consultation_system.j2") is True

    def test_exists_invalid_template(self):
        assert PromptLoader.exists("nonexistent/template.j2") is False

    def test_load_static_template(self):
        """验证 PromptLoader 可以加载 .j2 模板。"""
        # disclaimer 模板是纯文本无变量
        content = PromptLoader.load("validation/disclaimer.j2")
        assert isinstance(content, str)
        assert len(content) > 0

    def test_render_with_variables(self):
        """测试带变量的渲染（使用已知存在的模板）。"""
        # 使用 compression user 模板测试渲染
        if PromptLoader.exists("memory/compression_user.j2"):
            result = PromptLoader.render(
                "memory/compression_user.j2",
                dialogue_text="用户: 你好\n助手: 你好！"
            )
            assert isinstance(result, str)
            assert "你好" in result
