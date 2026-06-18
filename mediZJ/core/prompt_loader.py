"""
Prompt 模板加载器

基于 Jinja2 的集中式 prompt 管理，所有 prompt 模板存放在 prompt/ 目录下。
"""
from pathlib import Path
from typing import Any, Dict, Optional
from jinja2 import Environment, FileSystemLoader
from loguru import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_DIR = _PROJECT_ROOT / "prompt"


class PromptLoader:
    """集中式 Jinja2 prompt 模板加载器"""

    _env: Optional[Environment] = None
    _template_cache: Dict[str, Any] = {}
    _cache_enabled: bool = True

    @classmethod
    def _get_env(cls) -> Environment:
        if cls._env is None:
            if not _PROMPT_DIR.is_dir():
                raise FileNotFoundError(f"Prompt 目录不存在: {_PROMPT_DIR}")
            cls._env = Environment(
                loader=FileSystemLoader(str(_PROMPT_DIR)),
                autoescape=False,
                auto_reload=True,
                keep_trailing_newline=True,
                trim_blocks=False,
                lstrip_blocks=False,
            )
            logger.debug(f"PromptLoader 初始化完成，模板目录: {_PROMPT_DIR}")
        return cls._env

    @classmethod
    def render(cls, template_path: str, **kwargs: Any) -> str:
        """渲染带变量的 Jinja2 模板

        Args:
            template_path: prompt/ 下的相对路径，如 "agents/consultation_system.j2"
            **kwargs: 模板变量

        Returns:
            渲染后的字符串
        """
        if cls._cache_enabled and template_path in cls._template_cache:
            template = cls._template_cache[template_path]
        else:
            env = cls._get_env()
            template = env.get_template(template_path)
            if cls._cache_enabled:
                cls._template_cache[template_path] = template
        return template.render(**kwargs)

    @classmethod
    def load(cls, template_path: str) -> str:
        """加载静态模板（无变量渲染）"""
        return cls.render(template_path)

    @classmethod
    def exists(cls, template_path: str) -> bool:
        """检查模板文件是否存在"""
        return (_PROMPT_DIR / template_path).is_file()

    @classmethod
    def invalidate_cache(cls, template_path: Optional[str] = None):
        """清除模板缓存

        Args:
            template_path: 指定清除的模板路径，为 None 时清除全部缓存
        """
        if template_path:
            cls._template_cache.pop(template_path, None)
        else:
            cls._template_cache.clear()

    @classmethod
    def disable_cache(cls):
        """禁用模板缓存（开发/调试用）"""
        cls._cache_enabled = False
        cls._template_cache.clear()

    @classmethod
    def enable_cache(cls):
        """启用模板缓存"""
        cls._cache_enabled = True
