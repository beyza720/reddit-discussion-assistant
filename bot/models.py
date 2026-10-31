from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


def utcnow():
    return datetime.now(timezone.utc)


class Video(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=300)
    url: HttpUrl
    transcript_or_summary: str = Field(min_length=40, max_length=100000)
    keywords: list[str] = Field(min_length=1, max_length=20)


class RequestLimits(BaseModel):
    max_requests: int = Field(default=6, ge=1, le=50)
    min_interval_seconds: float = Field(default=5, ge=3, le=60)
    max_retries: int = Field(default=1, ge=0, le=2)
    timeout_seconds: int = Field(default=15, ge=1, le=30)
    cache_ttl_seconds: int = Field(default=300, ge=0, le=3600)


class Access(BaseModel):
    permission_reference: str = ''
    permission_note: str = 'No permission has been verified.'


class LLMConfig(BaseModel):
    provider: Literal['none', 'ollama', 'openai'] = 'none'
    model: str = 'qwen3:4b'
    max_calls: int = Field(default=5, ge=1, le=20)
    max_text_chars: int = Field(default=6000, ge=1000, le=12000)


class Config(BaseModel):
    videos: list[Video] = Field(min_length=1, max_length=10)
    subreddits: list[str] = Field(default_factory=list, max_length=10)
    max_age_days: int = Field(default=7, ge=1, le=30)
    max_results: int = Field(default=5, ge=1, le=20)
    requests: RequestLimits = Field(default_factory=RequestLimits)
    access: Access = Field(default_factory=Access)
    llm: LLMConfig = Field(default_factory=LLMConfig)

    @model_validator(mode='after')
    def validate_sources(self):
        import re
        if any(not re.fullmatch(r'[A-Za-z0-9_]{2,30}', s) for s in self.subreddits):
            raise ValueError('Invalid subreddit name')
        if len({v.id for v in self.videos}) != len(self.videos):
            raise ValueError('Video IDs must be unique')
        return self


@dataclass
class Discussion:
    title: str
    url: str
    subreddit: str
    published_at: datetime | None
    post_text: str
    locked: bool | None = None
    archived: bool | None = None
    comments: list[str] = field(default_factory=list)
    collected_at: datetime = field(default_factory=utcnow)
    sources: dict[str, str] = field(default_factory=dict)


@dataclass
class DiscoveryResult:
    discussions: list[Discussion] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    source_status: str = 'not_started'
