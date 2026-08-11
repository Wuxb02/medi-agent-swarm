"""自进化 API 数据模型。"""

from typing import List, Literal

from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    assistant_message_id: int = Field(gt=0)
    rating: Literal["like", "dislike"]
    reason_codes: List[
        Literal[
            "unsafe",
            "incomplete",
            "incorrect",
            "tool_misuse",
            "not_personalized",
        ]
    ] = Field(default_factory=list, max_length=10)
    comment: str = Field(default="", max_length=1000)


class ManualEvaluationRequest(BaseModel):
    assistant_message_id: int = Field(gt=0)


class ExperienceStatusRequest(BaseModel):
    action: Literal[
        "observe",
        "activate",
        "reject",
        "retire",
        "reapply",
        "delete",
    ]
