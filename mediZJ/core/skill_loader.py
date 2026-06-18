"""
Skill Loader 辅助函数
用于动态加载 .claude/skills 目录下的 Skill 函数（自动发现）
支持双层架构：Skill（能力包）= 指令正文 + 工具函数
"""
from pathlib import Path
import importlib.util
from typing import Callable, Dict, List, Optional
import yaml
import os
from loguru import logger

# Skill 发现缓存：避免每次 Agent 初始化都扫描磁盘 + 动态导入
_discovered_cache: Optional[List[Dict]] = None
_discovered_cache_root: Optional[Path] = None


def load_skill_function(skill_name: str, script_name: str, function_name: str, project_root: Path = None) -> Callable:
    """
    动态加载 Skill 函数

    Args:
        skill_name: Skill 目录名（如 "search-knowledge"）
        script_name: Python 脚本名（如 "search"）
        function_name: 函数名（如 "search_knowledge"）
        project_root: 项目根目录（如果为 None，自动检测）

    Returns:
        Skill 函数

    Example:
        search_knowledge = load_skill_function("search-knowledge", "search", "search_knowledge")
    """
    if project_root is None:
        # 自动检测项目根目录（当前文件在 mediZJ/core/ 目录）
        project_root = Path(__file__).parent.parent.parent

    skills_dir = project_root / ".claude" / "skills"
    module_path = skills_dir / skill_name / "script" / f"{script_name}.py"

    if not module_path.exists():
        raise FileNotFoundError(f"Skill module not found: {module_path}")

    # 动态加载模块
    spec = importlib.util.spec_from_file_location(f"skill_{skill_name.replace('-', '_')}", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 获取函数
    if not hasattr(module, function_name):
        raise AttributeError(f"Function '{function_name}' not found in {module_path}")

    return getattr(module, function_name)


def load_functions_from_script(skill_name: str, function_names: List[str], project_root: Path = None) -> Dict[str, Callable]:
    """
    从 skill 的 script/ 目录中加载指定的多个函数

    Args:
        skill_name: Skill 目录名
        function_names: 需要加载的函数名列表
        project_root: 项目根目录

    Returns:
        {函数名: 函数对象}
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent.parent

    skills_dir = project_root / ".claude" / "skills"
    script_dir = skills_dir / skill_name / "script"

    if not script_dir.exists():
        raise FileNotFoundError(f"Script directory not found: {script_dir}")

    # 加载 script/ 下所有 .py 模块
    script_files = [f for f in script_dir.iterdir() if f.suffix == '.py' and f.name != '__init__.py']

    if not script_files:
        raise FileNotFoundError(f"No Python script found in {script_dir}")

    # 加载所有模块
    modules = []
    for script_file in script_files:
        module_name = f"skill_{skill_name.replace('-', '_')}_{script_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, script_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)

    # 从模块中查找函数
    functions = {}
    for func_name in function_names:
        found = False
        for module in modules:
            if hasattr(module, func_name):
                functions[func_name] = getattr(module, func_name)
                found = True
                break
        if not found:
            logger.warning(f"Function '{func_name}' not found in {skill_name}/script/")

    return functions


def parse_skill_md(file_path: Path) -> Optional[Dict]:
    """
    解析 SKILL.md 或 skill.md 文件

    提取 YAML frontmatter（name, description, tools）和 Markdown body 正文

    Returns:
        {
            "name": "search-knowledge",
            "description": "...",
            "tools": ["search_knowledge"],    # 可选，未迁移时为空列表
            "instructions": "# Search...\n"   # SKILL.md 正文
        }
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 解析 YAML frontmatter
        if content.startswith('---'):
            end_idx = content.find('---', 3)
            if end_idx != -1:
                yaml_content = content[3:end_idx]
                try:
                    data = yaml.safe_load(yaml_content)
                except yaml.YAMLError as e:
                    logger.warning(f"Error parsing YAML in {file_path}: {e}")
                    return None

                # 提取正文（第二个 --- 之后的所有内容）
                body_start = end_idx + 3
                # 跳过 --- 后面的换行
                if body_start < len(content) and content[body_start] == '\n':
                    body_start += 1
                instructions = content[body_start:].strip()

                # 确保 tools 字段存在（兼容旧格式）
                if 'tools' not in data:
                    data['tools'] = []

                data['instructions'] = instructions
                return data

        return None
    except Exception as e:
        logger.warning(f"Error reading {file_path}: {e}")
        return None


def discover_skills(project_root: Path = None) -> List[Dict]:
    """
    自动扫描 .claude/skills 目录，发现所有 skills

    结果会缓存在模块级变量中，避免每次 Agent 初始化都扫描磁盘。
    开发期可通过 invalidate_skill_cache() 手动清除缓存。

    Args:
        project_root: 项目根目录（如果为 None，自动检测）

    Returns:
        [
            {
                "name": "search-knowledge",
                "function_name": "search_knowledge",     # 兼容旧格式
                "script_name": "search",                  # 兼容旧格式
                "metadata": { "name": "search-knowledge", "description": "...",
                              "tools": [...], "instructions": "..." },
                "function": <function>,                   # 兼容旧格式（主函数）
                "tool_functions": {"func_name": <fn>},    # 新格式（多函数）
                "migrated": True/False
            },
            ...
        ]
    """
    global _discovered_cache, _discovered_cache_root

    if project_root is None:
        project_root = Path(__file__).parent.parent.parent

    # 命中缓存直接返回
    if _discovered_cache is not None and _discovered_cache_root == project_root:
        logger.debug(f"Using cached skill discovery ({len(_discovered_cache)} skills)")
        return _discovered_cache

    skills_dir = project_root / ".claude" / "skills"

    if not skills_dir.exists():
        logger.warning(f"Skills directory not found: {skills_dir}")
        return []

    discovered_skills = []

    # 遍历所有子目录
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue

        skill_name = skill_dir.name

        # 查找 SKILL.md 或 skill.md
        skill_md_path = None
        for md_name in ["SKILL.md", "skill.md"]:
            test_path = skill_dir / md_name
            if test_path.exists():
                skill_md_path = test_path
                break

        if not skill_md_path:
            logger.debug(f"Skipping {skill_name}: no SKILL.md found")
            continue

        # 解析 frontmatter + body
        metadata = parse_skill_md(skill_md_path)
        if not metadata:
            logger.warning(f"Skipping {skill_name}: failed to parse SKILL.md")
            continue

        # 查找 script 目录
        script_dir = skill_dir / "script"
        if not script_dir.exists():
            logger.warning(f"Skipping {skill_name}: no script/ directory")
            continue

        # 检查是否有 tools 声明（新格式）
        declared_tools = metadata.get('tools', [])
        migrated = len(declared_tools) > 0

        if migrated:
            # 新格式：加载 tools 列表中声明的所有函数
            try:
                tool_functions = load_functions_from_script(skill_name, declared_tools, project_root)
                if not tool_functions:
                    logger.warning(f"Skipping {skill_name}: no functions loaded from tools list")
                    continue

                # 兼容旧字段：取第一个工具作为主函数
                primary_func_name = declared_tools[0]
                primary_func = tool_functions.get(primary_func_name)

                discovered_skills.append({
                    "name": skill_name,
                    "function_name": primary_func_name,
                    "script_name": None,  # 新格式不指定单个脚本
                    "metadata": metadata,
                    "function": primary_func,  # 兼容旧格式
                    "tool_functions": tool_functions,
                    "migrated": True
                })
                logger.info(f"✅ Discovered skill: {skill_name} (migrated, tools={declared_tools})")
            except Exception as e:
                logger.warning(f"⚠️ Skipping {skill_name}: {e}")
                continue
        else:
            # 旧格式兼容：取第一个脚本文件，按目录名推断函数名
            script_files = [f for f in script_dir.iterdir() if f.suffix == '.py' and f.name != '__init__.py']

            if not script_files:
                logger.warning(f"Skipping {skill_name}: no Python script found in script/")
                continue

            script_file = script_files[0]
            script_name = script_file.stem
            function_name = skill_name.replace('-', '_')

            try:
                func = load_skill_function(skill_name, script_name, function_name, project_root)
                discovered_skills.append({
                    "name": skill_name,
                    "function_name": function_name,
                    "script_name": script_name,
                    "metadata": metadata,
                    "function": func,
                    "tool_functions": {function_name: func},
                    "migrated": False
                })
                logger.info(f"✅ Discovered skill: {skill_name} (function={function_name}, legacy)")
            except (FileNotFoundError, AttributeError) as e:
                logger.warning(f"⚠️ Skipping {skill_name}: {e}")
                continue

    logger.info(f"Discovered {len(discovered_skills)} skills in total")
    _discovered_cache = discovered_skills
    _discovered_cache_root = project_root
    return discovered_skills


def invalidate_skill_cache():
    """清除 Skill 发现缓存，用于开发期手动刷新"""
    global _discovered_cache, _discovered_cache_root
    _discovered_cache = None
    _discovered_cache_root = None
    logger.info("Skill discovery cache invalidated")


def load_all_skills(project_root: Path = None) -> dict:
    """
    自动扫描并加载所有 Skills

    Returns:
        {
            "search_knowledge": <function>,
            "recommend_lifestyle": <function>,
            ...
        }
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent.parent

    discovered = discover_skills(project_root)

    skills = {}
    for skill_info in discovered:
        function_name = skill_info["function_name"]
        skills[function_name] = skill_info["function"]

    return skills
