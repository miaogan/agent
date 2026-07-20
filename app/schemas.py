from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class MessageItem(BaseModel):
    role: Role
    content: str
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class HistoryEvent(BaseModel):
    type: Literal["history"] = "history"
    content: list[MessageItem]


class TextChunkEvent(BaseModel):
    type: Literal["text_chunk"] = "text_chunk"
    content: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    name: str
    arguments: dict[str, Any]


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    name: str
    result: Any


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


Event = HistoryEvent | TextChunkEvent | ToolCallEvent | ToolResultEvent | ErrorEvent


class IssueStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class IssuePriority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class IssueCategory(str, Enum):
    BUG = "BUG"
    FEATURE = "FEATURE"
    IMPROVEMENT = "IMPROVEMENT"
    QUESTION = "QUESTION"
    DOCS = "DOCS"


class IssueSeverity(str, Enum):
    BLOCKER = "BLOCKER"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    TRIVIAL = "TRIVIAL"


class IssueDetail(BaseModel):
    issue_id: str
    title: str
    description: str
    status: IssueStatus
    priority: IssuePriority
    category: IssueCategory
    severity: IssueSeverity
    creator: str
    creator_email: str
    assignee: str
    assignee_email: str
    reporter: str
    created_at: str
    updated_at: str
    resolved_at: Optional[str]
    due_date: Optional[str]
    project: str
    module: str
    version: str
    environment: str
    fault_cause: str
    fault_component: str
    reproduction_steps: str
    expected_behavior: str
    actual_behavior: str
    tags: list[str]
    comments_count: int
    watchers_count: int
    votes_count: int
    attachments_count: int


class IssueListQuery(BaseModel):
    issue_id: Optional[str] = None
    title_keyword: Optional[str] = None
    status: Optional[list[IssueStatus]] = None
    priority: Optional[list[IssuePriority]] = None
    category: Optional[list[IssueCategory]] = None
    severity: Optional[list[IssueSeverity]] = None
    creator: Optional[str] = None
    assignee: Optional[str] = None
    project: Optional[str] = None
    module: Optional[str] = None
    version: Optional[str] = None
    environment: Optional[str] = None
    created_from: Optional[str] = None
    created_to: Optional[str] = None
    updated_from: Optional[str] = None
    updated_to: Optional[str] = None
    fault_component: Optional[str] = None
    tags_any: Optional[list[str]] = None
    page: int = 1
    page_size: int = 50


class IssueListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[IssueDetail]


class AggregationResult(BaseModel):
    group_key: str
    count: int
    items: list[dict[str, Any]]


class ChatRequest(BaseModel):
    user_input: str
    history: list[MessageItem] = Field(default_factory=list)
