"""个人信息路由（全局单文件）"""
from typing import Dict, List
from fastapi import APIRouter
from pydantic import BaseModel

from memory.personal_profile import PersonalProfile

router = APIRouter(prefix="/api/personal", tags=["personal"])

profile_manager = PersonalProfile()


class PersonalInfoItem(BaseModel):
    key: str
    value: str


class PersonalInfoResponse(BaseModel):
    info: Dict[str, str]
    items: List[PersonalInfoItem]


class PersonalInfoUpdate(BaseModel):
    items: List[PersonalInfoItem]


@router.get("", response_model=PersonalInfoResponse)
async def get_personal_info():
    """获取个人信息"""
    info = profile_manager.load()
    items = [PersonalInfoItem(key=k, value=v) for k, v in info.items()]
    return PersonalInfoResponse(info=info, items=items)


@router.put("", response_model=PersonalInfoResponse)
async def update_personal_info(body: PersonalInfoUpdate):
    """更新个人信息（全量替换）"""
    info_dict = {item.key: item.value for item in body.items if item.key.strip()}
    profile_manager.save(info_dict)
    items = [PersonalInfoItem(key=k, value=v) for k, v in info_dict.items()]
    return PersonalInfoResponse(info=info_dict, items=items)
