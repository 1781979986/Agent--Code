"""校验模型和请求的业务字段；JSON 字段名保持与前端协议一致。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import utf16_length


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", allow_inf_nan=False)


def valid_date(text):
    try:
        if len(text) != 10 or date.fromisoformat(text).isoformat() != text:
            raise ValueError()
    except ValueError:
        raise ValueError("日期须为有效 YYYY-MM-DD") from None
    return text


class Spec(StrictModel):
    kind: Literal["trend", "decline", "contribution", "summary"]
    metric: Literal["net", "paid", "refund"]
    start: str
    end: str
    region: str | None

    @field_validator("start", "end")
    @classmethod
    def dates(cls, text):
        return valid_date(text)


class Decision(StrictModel):
    route: Literal["query", "clarify", "insufficient"]
    spec: Spec | None
    sql: str | None
    message: str

    @field_validator("sql", "message")
    @classmethod
    def limits(cls, value, info):
        if value is not None and utf16_length(value) > (12000 if info.field_name == "sql" else 2000):
            raise ValueError("文本超过长度限制")
        return value


class PictureFacts(StrictModel):
    title: str
    start: str | None
    end: str | None
    metric: Literal["net", "paid", "refund", "unknown"]
    unit: Literal["元", "万元", "unknown"]
    region: str | None
    amount: float | None = Field(ge=0)
    uncertainties: list[str]

    @field_validator("start", "end")
    @classmethod
    def dates(cls, text):
        return valid_date(text) if text is not None else None
