"""
question_for_user 工具 — 向用户发送结构化问卷，收集诊断所需信息

支持三种题型：
- enum：单选（radio buttons）
- multi：多选（checkboxes）
- input：自由文本输入

从根目录 questio_for_user.py 迁移，移除 MCP 依赖，
改为 needs_user_input 标记模式，由 AgentLoop 处理暂停/恢复。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Callable, Optional

from loguru import logger


# ===== Pydantic XML 模型 =====

try:
    from pydantic_xml import BaseXmlModel, attr, element

    class Suggest(BaseXmlModel, tag="suggest"):
        """问题中的选项"""
        type: str = attr(default="choice")
        description: str | None = attr(default=None)
        next_action: str | None = attr(default=None)
        label: str  # 元素文本

    class Question(BaseXmlModel, tag="question"):
        """单个问题及其选项"""
        header: str = attr()
        type: str = attr(default="enum")
        required: bool = attr(default=True)
        text: str = element(tag="text")
        options: list[Suggest] = element(tag="suggest", default=[])

    class Questions(BaseXmlModel, tag="questions"):
        """多个问题的容器"""
        questions: list[Question] = element(tag="question")

    _HAS_PYDANTIC_XML = True
except ImportError:
    logger.warning("pydantic_xml not available, questionnaire XML parsing disabled")
    _HAS_PYDANTIC_XML = False


# ===== XML 解析 =====

def parse_questionnaire(xml: str):
    """解析 XML 问卷为 Question 对象列表。

    Args:
        xml: XML 字符串，需包含 <questions> 包裹的 <question> 标签，
             或向后兼容的裸 <question> 标签。

    Returns:
        解析后的 Question 对象列表
    """
    if not _HAS_PYDANTIC_XML:
        raise ImportError("pydantic_xml is required for questionnaire parsing")

    xml = xml.strip()
    if not xml.startswith("<questions"):
        xml = f"<questions>{xml}</questions>"
    return Questions.from_xml(xml).questions


# ===== Schema 构建（供前端校验） =====

def _build_acp_schema(questions) -> Dict[str, Any]:
    """从 Question 列表构建 JSON Schema（用于前端表单校验）"""
    properties: Dict[str, Any] = {}
    required: list[str] = []

    for i, q in enumerate(questions):
        key = f"q{i}"
        if q.required:
            required.append(key)

        if q.type == "enum":
            properties[key] = {
                "type": "string",
                "title": q.header,
                "description": q.text,
                "oneOf": [
                    {"const": o.label, **({"title": o.description} if o.description else {})}
                    for o in q.options
                ],
            }
        elif q.type == "multi":
            option_labels = [o.label for o in q.options]
            descriptions = {o.label: o.description for o in q.options if o.description}
            multi_schema: Dict[str, Any] = {
                "type": "array",
                "title": q.header,
                "description": q.text,
                "items": {"type": "string", "enum": option_labels},
                "uniqueItems": True,
            }
            if descriptions:
                multi_schema["items"]["x-option-descriptions"] = descriptions
            properties[key] = multi_schema
        elif q.type == "input":
            properties[key] = {
                "type": "string",
                "title": q.header,
                "description": q.text,
                "minLength": 1,
            }

    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


# ===== 答案格式化 =====

def format_answers_for_llm(questions, answers: Dict[str, Any]) -> str:
    """将用户答案格式化为 LLM 可读的文本。

    Args:
        questions: Question 对象列表
        answers: 用户答案字典，key 为 "q0", "q1" 等

    Returns:
        格式化后的文本，如 "年龄: 35\n性别: 男\n症状: 头痛, 发热"
    """
    parts = []
    for i, q in enumerate(questions):
        key = f"q{i}"
        value = answers.get(key)
        if q.type == "multi" and isinstance(value, list):
            parts.append(f"{q.header}: {', '.join(str(v) for v in value)}")
        elif value is not None:
            parts.append(f"{q.header}: {value}")
        else:
            parts.append(f"{q.header}: (未回答)")
    return "\n".join(parts)


def _build_questionnaire_data(questions) -> Dict[str, Any]:
    """构建前端所需的结构化问卷数据"""
    return {
        "questions": [
            {
                "header": q.header,
                "type": q.type,
                "required": q.required,
                "text": q.text,
                "options": [
                    {"label": o.label, **({"description": o.description} if o.description else {})}
                    for o in q.options
                ],
            }
            for q in questions
        ]
    }


# ===== 工具工厂 =====

def create_question_for_user_tool(manager_getter: Callable) -> Callable:
    """创建 question_for_user 工具函数（闭包模式，捕获 manager 引用）

    Args:
        manager_getter: 返回 QuestionnaireManager 实例的函数

    Returns:
        question_for_user 异步函数
    """

    async def question_for_user(questionnaire: str) -> Dict[str, Any]:
        """向用户发送结构化问卷，收集诊断所需信息。

        支持单选(enum)、多选(multi)、文本输入(input)三种题型。
        工具调用后会暂停 Agent 执行，等待用户填写并提交答案后继续。

        Args:
            questionnaire: XML 格式的问卷，包含 <questions> 标签包裹的 <question> 元素

        Returns:
            包含 needs_user_input 标记的字典（由 AgentLoop 处理暂停/恢复）
        """
        manager = manager_getter()
        if not manager:
            return {
                "success": False,
                "error": "QuestionnaireManager 不可用，无法进行交互式提问"
            }

        try:
            questions = parse_questionnaire(questionnaire)
        except Exception as e:
            return {
                "success": False,
                "error": f"问卷解析失败: {e}"
            }

        if not questions:
            return {
                "success": False,
                "error": "问卷中没有问题"
            }

        questionnaire_id = str(uuid.uuid4())
        questionnaire_data = _build_questionnaire_data(questions)

        logger.info(f"创建问卷 {questionnaire_id}，共 {len(questions)} 个问题")

        # 返回 needs_user_input 标记，由 AgentLoop 处理暂停
        return {
            "needs_user_input": True,
            "questionnaire_id": questionnaire_id,
            "questionnaire_data": questionnaire_data,
            # 传递格式化函数所需的元数据（供 AgentLoop 使用）
            "_questions_ref": questions,
        }

    return question_for_user
