from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=512)


class AccountLoginRequest(LoginRequest):
    headless: bool = False


class AccountUpdate(StrictModel):
    username: str | None = Field(default=None, min_length=1, max_length=100)
    password: str | None = Field(default=None, max_length=512)


class PlatformStatusUpdate(StrictModel):
    status: Literal[1, 3]


class KeywordCreate(StrictModel):
    keyword: str = Field(min_length=1, max_length=100)


class KeywordUpdate(KeywordCreate):
    pass


class KeywordImport(StrictModel):
    content: str = Field(min_length=1, max_length=100_000)


class ProductKnowledgeUpdate(StrictModel):
    specifications: str | None = Field(default=None, max_length=100_000)
    extracted_content: str | None = Field(default=None, max_length=500_000)


class ProductSyncRequest(StrictModel):
    mode: Literal["incremental", "full"] = "incremental"


class CustomerKnowledgeCreate(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=500_000)
    tags: str | None = Field(default=None, max_length=255)
    enabled: bool = True


class CustomerKnowledgeUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=500_000)
    tags: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None


class LLMSettings(StrictModel):
    provider: str = Field(min_length=1, max_length=50)
    model_name: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=4096)
    api_base: str = Field(default="", max_length=2048)
    endpoint_trust_mode: str = Field(default="default", max_length=20)
    tool_policy: str = Field(default="enabled", max_length=20)
    confirm_risk: bool = False


class BusinessHoursSettings(StrictModel):
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        import datetime as dt

        try:
            dt.datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("时间格式必须为 HH:MM") from exc
        return value


class PromptSettings(StrictModel):
    instructions: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, values: list[str]) -> list[str]:
        result = [value.strip() for value in values if value.strip()]
        if any(len(value) > 2000 for value in result):
            raise ValueError("单条指令不能超过 2000 个字符")
        return result


class SettingsUpdate(StrictModel):
    version: str = Field(min_length=1, max_length=128)
    llm: LLMSettings
    business_hours: BusinessHoursSettings
    prompt: PromptSettings


__all__ = [name for name in globals() if not name.startswith("_")]

