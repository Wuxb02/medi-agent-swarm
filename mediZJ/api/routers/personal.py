"""个人信息路由（三层架构：个人中心 + 待确认 + 病史记录）"""
from typing import Dict, List, Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel

from mediZJ.memory.personal_profile import PersonalProfile

router = APIRouter(prefix="/api/personal", tags=["personal"])

# 默认用户（未传 user_id 时的兼容行为）
_default_profile = PersonalProfile()


def _get_profile(user_id: Optional[str]) -> PersonalProfile:
    """按 user_id 获取档案管理器；缺省返回默认用户"""
    if not user_id:
        return _default_profile
    return PersonalProfile(user_id=user_id)


# ========== Pydantic 模型 ==========

class PersonalInfoItem(BaseModel):
    key: str
    value: str


class PendingItemSchema(BaseModel):
    key: str
    value: str
    source_date: str
    confidence: str
    is_record: bool = False
    record_date: str = ""
    symptoms: str = ""
    duration: str = ""
    medication: str = ""
    outcome: str = ""


class MedicalRecordSchema(BaseModel):
    date: str
    description: str
    symptoms: str = ""
    duration: str = ""
    medication: str = ""
    outcome: str = ""


class PersonalInfoResponse(BaseModel):
    info: Dict[str, str]
    items: List[PersonalInfoItem]
    pending_items: List[PendingItemSchema] = []
    medical_records: List[MedicalRecordSchema] = []


class PersonalInfoUpdate(BaseModel):
    items: List[PersonalInfoItem]


class PendingConfirmRequest(BaseModel):
    key: str
    value: str


class MedicalRecordsUpdate(BaseModel):
    records: List[MedicalRecordSchema]


# ========== 个人中心（已确认信息） ==========

@router.get("", response_model=PersonalInfoResponse)
async def get_personal_info(
    user_id: Optional[str] = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"),
):
    """获取个人信息（已确认 + 待确认 + 病史记录）"""
    profile_manager = _get_profile(user_id)
    # 已确认信息
    info = profile_manager.load()
    items = [PersonalInfoItem(key=k, value=v) for k, v in info.items()]

    # 待确认信息
    pending = profile_manager.load_pending()
    pending_items = [
        PendingItemSchema(
            key=p.key, value=p.value,
            source_date=p.source_date, confidence=p.confidence,
            is_record=p.is_record,
            record_date=p.record_date,
            symptoms=p.symptoms,
            duration=p.duration,
            medication=p.medication,
            outcome=p.outcome,
        )
        for p in pending
    ]

    # 病史记录
    records = profile_manager.load_records()
    medical_records = [
        MedicalRecordSchema(
            date=r.date, description=r.description,
            symptoms=r.symptoms, duration=r.duration,
            medication=r.medication, outcome=r.outcome,
        )
        for r in records
    ]

    return PersonalInfoResponse(
        info=info, items=items,
        pending_items=pending_items,
        medical_records=medical_records,
    )


@router.put("", response_model=PersonalInfoResponse)
async def update_personal_info(
    body: PersonalInfoUpdate,
    user_id: Optional[str] = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"),
):
    """更新个人信息（全量替换已确认信息）"""
    profile_manager = _get_profile(user_id)
    info_dict = {item.key: item.value for item in body.items if item.key.strip()}
    profile_manager.save(info_dict)

    items = [PersonalInfoItem(key=k, value=v) for k, v in info_dict.items()]

    # 重新加载待确认和病史
    pending = profile_manager.load_pending()
    pending_items = [
        PendingItemSchema(
            key=p.key, value=p.value,
            source_date=p.source_date, confidence=p.confidence,
            is_record=p.is_record,
            record_date=p.record_date,
            symptoms=p.symptoms,
            duration=p.duration,
            medication=p.medication,
            outcome=p.outcome,
        )
        for p in pending
    ]
    records = profile_manager.load_records()
    medical_records = [
        MedicalRecordSchema(
            date=r.date, description=r.description,
            symptoms=r.symptoms, duration=r.duration,
            medication=r.medication, outcome=r.outcome,
        )
        for r in records
    ]

    return PersonalInfoResponse(
        info=info_dict, items=items,
        pending_items=pending_items,
        medical_records=medical_records,
    )


# ========== 待确认暂存区 ==========

@router.post("/pending/confirm")
async def confirm_pending_item(
    body: PendingConfirmRequest,
    user_id: Optional[str] = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"),
):
    """确认待确认条目：从暂存区移入已确认信息"""
    # confirm_pending 内部会同时：从 PENDING.md 删除 + 写入 CONFIRMED.md
    success = _get_profile(user_id).confirm_pending(body.key, body.value)
    if not success:
        return {"status": "not_found", "key": body.key, "value": body.value}
    return {"status": "ok", "key": body.key, "value": body.value}


@router.post("/pending/dismiss")
async def dismiss_pending_item(
    body: PendingConfirmRequest,
    user_id: Optional[str] = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"),
):
    """丢弃待确认条目"""
    _get_profile(user_id).dismiss_pending(body.key, body.value)
    return {"status": "ok", "key": body.key, "value": body.value}


# ========== 病史记录 ==========

@router.get("/records")
async def get_medical_records(
    user_id: Optional[str] = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"),
):
    """获取病史记录列表"""
    records = _get_profile(user_id).load_records()
    return {
        "records": [
            {
                "date": r.date,
                "description": r.description,
                "symptoms": r.symptoms,
                "duration": r.duration,
                "medication": r.medication,
                "outcome": r.outcome,
            }
            for r in records
        ]
    }


@router.put("/records")
async def update_medical_records(
    body: MedicalRecordsUpdate,
    user_id: Optional[str] = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"),
):
    """更新病史记录（全量替换）"""
    from mediZJ.memory.personal_profile import MedicalRecord
    records = [
        MedicalRecord(
            date=r.date, description=r.description,
            symptoms=r.symptoms, duration=r.duration,
            medication=r.medication, outcome=r.outcome,
        )
        for r in body.records
    ]
    _get_profile(user_id).save_records(records)
    return {"status": "ok", "count": len(records)}
