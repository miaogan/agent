from __future__ import annotations

import json
import random
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from app.schemas import (
    IssueCategory,
    IssueDetail,
    IssueListQuery,
    IssueListResponse,
    IssuePriority,
    IssueSeverity,
    IssueStatus,
)

DB_PATH = Path(__file__).parent.parent / "data" / "issues.db"

# -------- Fallback in-memory data (used only when SQLite not seeded) --------

STATUSES = list(IssueStatus)
PRIORITIES = list(IssuePriority)
CATEGORIES = list(IssueCategory)
SEVERITIES = list(IssueSeverity)

CREATORS_FB = [
    "张伟", "李娜", "王强", "刘洋", "陈静", "杨帆", "赵磊", "黄敏",
    "周杰", "吴婷", "徐浩", "孙丽", "马超", "朱琳", "胡军",
]
PROJECTS_FB = [
    "订单管理系统", "用户中心", "支付网关", "消息推送平台",
    "数据分析平台", "权限管理系统", "商品中心", "物流追踪系统",
]
MODULES_FB = [
    "登录认证", "API网关", "数据库层", "缓存层", "消息队列",
    "前端页面", "定时任务", "报表模块", "搜索服务", "文件存储",
]
VERSIONS_FB = ["v1.0.0", "v1.2.3", "v2.0.0", "v2.1.5", "v3.0.0-beta", "v1.5.2"]
ENVIRONMENTS_FB = ["生产环境", "预发布环境", "测试环境", "开发环境"]
FAULT_CAUSES_FB = [
    "空指针异常导致服务崩溃", "数据库连接池耗尽", "缓存击穿引发DB压力",
    "接口超时未降级", "并发竞态条件", "配置错误导致路由失败",
    "依赖服务不可用", "资源泄漏(内存/文件句柄)", "序列化/反序列化异常",
    "权限校验绕过漏洞",
]
FAULT_COMPONENTS_FB = [
    "UserService", "OrderController", "PaymentClient", "MQConsumer",
    "CacheManager", "AuthFilter", "Scheduler", "ReportEngine",
    "SearchIndexer", "FileUploader",
]
TAGS_POOL_FB = [
    "urgent", "regression", "security", "performance", "ui",
    "backend", "frontend", "database", "network", "api",
    "blocker", "customer-reported", "needs-reproduction",
]


def _random_time(base: datetime, days_back: int = 30) -> datetime:
    if days_back <= 0:
        days_back = 1
    return base - timedelta(
        days=random.randint(0, days_back - 1),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )


def _generate_issue_fb(issue_id: Optional[str] = None) -> IssueDetail:
    now = datetime.now()
    created = _random_time(now, 60)
    updated = created + timedelta(hours=random.randint(1, 120))
    status = random.choice(STATUSES)
    resolved = None
    if status in (IssueStatus.RESOLVED, IssueStatus.CLOSED):
        resolved = (updated + timedelta(hours=random.randint(1, 72))).isoformat()
    creator = random.choice(CREATORS_FB)
    assignee = random.choice([c for c in CREATORS_FB if c != creator])
    tags = random.sample(TAGS_POOL_FB, k=random.randint(0, 4))
    return IssueDetail(
        issue_id=issue_id or f"ISS-{random.randint(10000, 99999)}",
        title=random.choice([
            "登录页面验证码加载失败问题", "订单状态更新延迟超过5分钟",
            "支付回调偶发丢失导致订单未完成", "消息推送重复发送用户投诉",
            "大数据量报表导出内存溢出", "权限管理批量操作接口超时",
            "商品详情页图片CDN回源失败", "物流信息同步不及时",
            "高并发下库存扣减不一致", "搜索结果排序规则异常",
            "用户头像上传后无法显示", "移动端首页加载白屏",
            "定时任务执行失败未告警", "第三方接口调用SSL握手失败",
            "管理后台分页查询总数错误",
        ]),
        description=random.choice([
            "用户反馈在特定操作路径下出现异常，已收集到日志样本。",
            "该问题在高峰期复现概率较高，怀疑与并发量相关。",
            "新上线功能引入的回归问题，影响核心流程。",
            "历史遗留问题，近期业务量增长后凸显。",
            "经初步排查，涉及多个模块协作链路。",
        ]),
        status=status,
        priority=random.choice(PRIORITIES),
        category=random.choice(CATEGORIES),
        severity=random.choice(SEVERITIES),
        creator=creator,
        creator_email=f"{creator.lower()}@example.com",
        assignee=assignee,
        assignee_email=f"{assignee.lower()}@example.com",
        reporter=random.choice(CREATORS_FB),
        created_at=created.isoformat(),
        updated_at=updated.isoformat(),
        resolved_at=resolved,
        due_date=(now + timedelta(days=random.randint(1, 20))).isoformat() if random.random() > 0.4 else None,
        project=random.choice(PROJECTS_FB),
        module=random.choice(MODULES_FB),
        version=random.choice(VERSIONS_FB),
        environment=random.choice(ENVIRONMENTS_FB),
        fault_cause=random.choice(FAULT_CAUSES_FB),
        fault_component=random.choice(FAULT_COMPONENTS_FB),
        reproduction_steps="1. 登录系统 2. 进入目标页面 3. 执行触发操作 4. 观察异常",
        expected_behavior="操作执行成功，页面正常展示，数据持久化正确。",
        actual_behavior="出现异常提示/页面崩溃/数据不一致，相关功能不可用。",
        tags=tags,
        comments_count=random.randint(0, 45),
        watchers_count=random.randint(1, 20),
        votes_count=random.randint(0, 30),
        attachments_count=random.randint(0, 8),
    )


_ALL_ISSUES_FB: list[IssueDetail] = [_generate_issue_fb() for _ in range(200)]


def _matches_query_fb(issue: IssueDetail, query: IssueListQuery) -> bool:
    if query.issue_id and query.issue_id not in issue.issue_id:
        return False
    if query.title_keyword and query.title_keyword not in issue.title:
        return False
    if query.status and issue.status not in query.status:
        return False
    if query.priority and issue.priority not in query.priority:
        return False
    if query.category and issue.category not in query.category:
        return False
    if query.severity and issue.severity not in query.severity:
        return False
    if query.creator and issue.creator != query.creator:
        return False
    if query.assignee and issue.assignee != query.assignee:
        return False
    if query.project and issue.project != query.project:
        return False
    if query.module and issue.module != query.module:
        return False
    if query.version and issue.version != query.version:
        return False
    if query.environment and issue.environment != query.environment:
        return False
    if query.fault_component and query.fault_component not in issue.fault_component:
        return False
    if query.created_from and issue.created_at < query.created_from:
        return False
    if query.created_to and issue.created_at > query.created_to:
        return False
    if query.updated_from and issue.updated_at < query.updated_from:
        return False
    if query.updated_to and issue.updated_at > query.updated_to:
        return False
    if query.tags_any and not any(t in issue.tags for t in query.tags_any):
        return False
    return True


# ----------------------------- SQLite backend -----------------------------

_SQLITE_CHECKED = False
_USE_SQLITE = False
_LOCK = threading.Lock()


def _ensure_backend() -> None:
    global _SQLITE_CHECKED, _USE_SQLITE
    with _LOCK:
        if _SQLITE_CHECKED:
            return
        _SQLITE_CHECKED = True
        if DB_PATH.exists():
            try:
                conn = sqlite3.connect(str(DB_PATH))
                try:
                    cur = conn.execute("SELECT COUNT(*) FROM issues")
                    n = cur.fetchone()[0]
                    if n > 0:
                        _USE_SQLITE = True
                        print(f"[mock_remote_api] 使用 SQLite 数据源: {DB_PATH} (记录数={n})")
                finally:
                    conn.close()
            except Exception as e:  # noqa: BLE001
                print(f"[mock_remote_api] 打开 SQLite 失败，降级内存数据: {e}")
        if not _USE_SQLITE:
            print(f"[mock_remote_api] 使用内存 fallback 数据源 ({len(_ALL_ISSUES_FB)} 条)")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


ISSUE_COLUMNS = [
    "issue_id", "title", "description", "status", "priority", "category",
    "severity", "creator", "creator_email", "assignee", "assignee_email",
    "reporter", "created_at", "updated_at", "resolved_at", "due_date",
    "project", "module", "version", "environment", "fault_cause",
    "fault_component", "reproduction_steps", "expected_behavior",
    "actual_behavior", "tags", "comments_count", "watchers_count",
    "votes_count", "attachments_count",
]


def _row_to_detail(row: Any) -> IssueDetail:
    return IssueDetail(
        issue_id=row["issue_id"],
        title=row["title"],
        description=row["description"],
        status=row["status"],
        priority=row["priority"],
        category=row["category"],
        severity=row["severity"],
        creator=row["creator"],
        creator_email=row["creator_email"],
        assignee=row["assignee"],
        assignee_email=row["assignee_email"],
        reporter=row["reporter"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        resolved_at=row["resolved_at"],
        due_date=row["due_date"],
        project=row["project"],
        module=row["module"],
        version=row["version"],
        environment=row["environment"],
        fault_cause=row["fault_cause"],
        fault_component=row["fault_component"],
        reproduction_steps=row["reproduction_steps"],
        expected_behavior=row["expected_behavior"],
        actual_behavior=row["actual_behavior"],
        tags=json.loads(row["tags"] or "[]"),
        comments_count=row["comments_count"],
        watchers_count=row["watchers_count"],
        votes_count=row["votes_count"],
        attachments_count=row["attachments_count"],
    )


def _build_where(query: IssueListQuery) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query.issue_id:
        clauses.append("issue_id LIKE ?")
        params.append(f"%{query.issue_id}%")
    if query.title_keyword:
        clauses.append("(title LIKE ? OR description LIKE ?)")
        params.extend([f"%{query.title_keyword}%", f"%{query.title_keyword}%"])
    if query.status:
        placeholders = ",".join(["?"] * len(query.status))
        clauses.append(f"status IN ({placeholders})")
        params.extend(list(query.status))
    if query.priority:
        placeholders = ",".join(["?"] * len(query.priority))
        clauses.append(f"priority IN ({placeholders})")
        params.extend(list(query.priority))
    if query.category:
        placeholders = ",".join(["?"] * len(query.category))
        clauses.append(f"category IN ({placeholders})")
        params.extend(list(query.category))
    if query.severity:
        placeholders = ",".join(["?"] * len(query.severity))
        clauses.append(f"severity IN ({placeholders})")
        params.extend(list(query.severity))
    if query.creator:
        clauses.append("creator = ?")
        params.append(query.creator)
    if query.assignee:
        clauses.append("assignee = ?")
        params.append(query.assignee)
    if query.project:
        clauses.append("project = ?")
        params.append(query.project)
    if query.module:
        clauses.append("module = ?")
        params.append(query.module)
    if query.version:
        clauses.append("version = ?")
        params.append(query.version)
    if query.environment:
        clauses.append("environment = ?")
        params.append(query.environment)
    if query.fault_component:
        clauses.append("fault_component LIKE ?")
        params.append(f"%{query.fault_component}%")
    if query.created_from:
        clauses.append("created_at >= ?")
        params.append(query.created_from)
    if query.created_to:
        clauses.append("created_at <= ?")
        params.append(query.created_to)
    if query.updated_from:
        clauses.append("updated_at >= ?")
        params.append(query.updated_from)
    if query.updated_to:
        clauses.append("updated_at <= ?")
        params.append(query.updated_to)
    if query.tags_any:
        ors = []
        for t in query.tags_any:
            ors.append("tags LIKE ?")
            params.append(f'%"{t}"%')
        if ors:
            clauses.append("(" + " OR ".join(ors) + ")")
    sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return sql, params


def query_issue_list(query: IssueListQuery) -> IssueListResponse:
    """列表查询接口：优先 SQLite，失败降级内存。"""
    _ensure_backend()
    if _USE_SQLITE:
        where_sql, params = _build_where(query)
        cols = ",".join(ISSUE_COLUMNS)
        conn = _conn()
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM issues{where_sql}", params
            ).fetchone()[0]
            offset = (query.page - 1) * query.page_size
            rows = conn.execute(
                f"SELECT {cols} FROM issues{where_sql} "
                f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [int(query.page_size), int(offset)],
            ).fetchall()
            items = [_row_to_detail(r) for r in rows]
        finally:
            conn.close()
        return IssueListResponse(
            total=total,
            page=query.page,
            page_size=query.page_size,
            items=items,
        )
    filtered = [i for i in _ALL_ISSUES_FB if _matches_query_fb(i, query)]
    total = len(filtered)
    start = (query.page - 1) * query.page_size
    end = start + query.page_size
    return IssueListResponse(
        total=total,
        page=query.page,
        page_size=query.page_size,
        items=filtered[start:end],
    )


def query_issue_detail(issue_id: str) -> Optional[IssueDetail]:
    """详情查询接口：优先 SQLite，失败降级内存。"""
    _ensure_backend()
    if _USE_SQLITE:
        cols = ",".join(ISSUE_COLUMNS)
        conn = _conn()
        try:
            row = conn.execute(
                f"SELECT {cols} FROM issues WHERE issue_id = ?", (issue_id,)
            ).fetchone()
            if not row:
                row = conn.execute(
                    f"SELECT {cols} FROM issues WHERE issue_id LIKE ? LIMIT 1",
                    (f"%{issue_id}%",),
                ).fetchone()
            return _row_to_detail(row) if row else None
        finally:
            conn.close()
    for issue in _ALL_ISSUES_FB:
        if issue.issue_id == issue_id:
            return issue
    for issue in _ALL_ISSUES_FB:
        if issue_id in issue.issue_id:
            return issue
    return None
