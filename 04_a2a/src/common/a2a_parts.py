"""A2A 메시지/파트 헬퍼 (a2a-sdk 1.x, protobuf 타입)."""

import uuid

from a2a.helpers import get_data_parts, new_data_part, new_text_part
from a2a.types import Message, Role


def normalize(value):
    """DataPart는 protobuf Value라 정수가 float로 돌아온다(1 -> 1.0). 정수로 되돌린다."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def first_data(parts) -> dict:
    """파트 목록에서 첫 번째 DataPart(dict)를 꺼낸다."""
    for item in get_data_parts(parts):
        if isinstance(item, dict):
            return normalize(item)
    return {}


def user_message(text: str, data: dict | None = None, task_id: str = "", context_id: str = "") -> Message:
    parts = [new_text_part(text)]
    if data is not None:
        parts.append(new_data_part(data))
    return Message(role=Role.ROLE_USER, message_id=str(uuid.uuid4()), parts=parts, task_id=task_id, context_id=context_id)
