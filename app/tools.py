from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool

from app.mock_remote_api import query_issue_detail, query_issue_list
from app.schemas import IssueListQuery


@tool
def search_issue_list(
    issue_id: Optional[str] = None,
    title_keyword: Optional[str] = None,
    status: Optional[list[str]] = None,
    priority: Optional[list[str]] = None,
    category: Optional[list[str]] = None,
    severity: Optional[list[str]] = None,
    creator: Optional[str] = None,
    assignee: Optional[str] = None,
    project: Optional[str] = None,
    module: Optional[str] = None,
    version: Optional[str] = None,
    environment: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    updated_from: Optional[str] = None,
    updated_to: Optional[str] = None,
    fault_component: Optional[str] = None,
    tags_any: Optional[list[str]] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """问题单列表查询。status枚举: OPEN/IN_PROGRESS/PENDING/RESOLVED/CLOSED/REOPENED。
    priority: CRITICAL/HIGH/MEDIUM/LOW。category: BUG/FEATURE/IMPROVEMENT/QUESTION/DOCS。
    severity: BLOCKER/MAJOR/MINOR/TRIVIAL。返回 {total, page, page_size, items: [问题单30字段对象]}。"""
    query = IssueListQuery(
        issue_id=issue_id,
        title_keyword=title_keyword,
        status=status,
        priority=priority,
        category=category,
        severity=severity,
        creator=creator,
        assignee=assignee,
        project=project,
        module=module,
        version=version,
        environment=environment,
        created_from=created_from,
        created_to=created_to,
        updated_from=updated_from,
        updated_to=updated_to,
        fault_component=fault_component,
        tags_any=tags_any,
        page=page,
        page_size=page_size,
    )
    result = query_issue_list(query)
    return result.model_dump(mode="json")


@tool
def get_issue_detail(issue_id: str) -> dict:
    """按 issue_id(如 ISS-12345) 查询单条问题单详情，返回 30 字段完整对象。"""
    detail = query_issue_detail(issue_id)
    return detail.model_dump(mode="json") if detail else {}
