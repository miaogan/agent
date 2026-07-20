from __future__ import annotations

from app.schemas import MessageItem, Role

MAX_ROUNDS = 3


def _count_user_rounds(messages: list[MessageItem]) -> int:
    return sum(1 for m in messages if m.role == Role.USER)


def trim_history(messages: list[MessageItem], max_rounds: int = MAX_ROUNDS) -> list[MessageItem]:
    """无状态截断：只保留最近 N 轮（一个 user+assistant 对算一轮）。
    由客户端每次传入完整 history，服务端仅做截断，不持久化任何数据。
    """
    if not messages:
        return []

    total_rounds = _count_user_rounds(messages)
    if total_rounds <= max_rounds:
        return list(messages)

    rounds_to_drop = total_rounds - max_rounds
    dropped = 0
    idx = 0
    while idx < len(messages) and dropped < rounds_to_drop:
        if messages[idx].role == Role.USER:
            dropped += 1
        idx += 1

    while idx < len(messages) and messages[idx].role != Role.USER:
        idx += 1

    return list(messages[idx:])


def extract_context_for_query(
    history: list[MessageItem],
    current_input: str,
) -> dict:
    """从近三轮历史中提取与当前问题相关的上下文信息：
    - 历史中提到的筛选条件（项目、状态、处理人等）
    - 已查询过的issue_id供引用
    - 用户偏好的展示方式
    """
    context: dict[str, Any] = {
        "mentioned_issue_ids": [],
        "mentioned_projects": [],
        "mentioned_assignees": [],
        "mentioned_statuses": [],
        "preferred_group_by": None,
        "preferred_date_agg": None,  # {"date_field": "created_at", "granularity": "month"}
    }

    import re

    recent_texts: list[str] = []
    for m in history:
        if m.role in (Role.USER, Role.ASSISTANT):
            recent_texts.append(m.content)
    recent_texts.append(current_input)
    full_text = "\n".join(recent_texts)

    issue_ids = re.findall(r"ISS[-_]?\d{3,6}", full_text, re.IGNORECASE)
    context["mentioned_issue_ids"] = list(dict.fromkeys(issue_ids))

    status_map = {
        "打开": "OPEN", "待处理": "OPEN", "open": "OPEN",
        "处理中": "IN_PROGRESS", "进行中": "IN_PROGRESS", "in_progress": "IN_PROGRESS",
        "待审核": "PENDING", "pending": "PENDING",
        "已解决": "RESOLVED", "resolved": "RESOLVED",
        "已关闭": "CLOSED", "closed": "CLOSED",
        "重新打开": "REOPENED", "reopened": "REOPENED",
    }
    for k, v in status_map.items():
        if k in full_text.lower() or k in full_text:
            if v not in context["mentioned_statuses"]:
                context["mentioned_statuses"].append(v)

    project_keywords = [
        "订单管理系统", "用户中心", "支付网关", "消息推送平台",
        "数据分析平台", "权限管理系统", "商品中心", "物流追踪系统",
    ]
    for p in project_keywords:
        if p in full_text:
            context["mentioned_projects"].append(p)

    people = [
        "张伟", "李娜", "王强", "刘洋", "陈静", "杨帆", "赵磊", "黄敏",
        "周杰", "吴婷", "徐浩", "孙丽", "马超", "朱琳", "胡军",
    ]
    for n in people:
        if n in full_text and n not in context["mentioned_assignees"]:
            context["mentioned_assignees"].append(n)

    group_hints = [
        ("按状态", "status"), ("分组状态", "status"),
        ("按优先级", "priority"), ("分组优先级", "priority"),
        ("按项目", "project"), ("分组项目", "project"),
        ("按模块", "module"), ("分组模块", "module"),
        ("按处理人", "assignee"), ("分组处理人", "assignee"),
        ("按严重程度", "severity"), ("按环境", "environment"),
    ]
    for hint, field in group_hints:
        if hint in full_text:
            context["preferred_group_by"] = field
            break

    # ------ 时间维度（按年/季/月/周/日/小时）------
    # 粒度关键字 -> granularity
    granularity_hints = [
        ("按年", "year"), ("每年", "year"), ("年度", "year"), ("按年份", "year"),
        ("按季度", "quarter"), ("每季", "quarter"), ("季度", "quarter"),
        ("按月", "month"), ("每月", "month"), ("月度", "month"), ("按月份", "month"),
        ("按周", "week"), ("每周", "week"), ("周度", "week"),
        ("按日", "day"), ("每日", "day"), ("天", "day"), ("按天", "day"), ("日报", "day"),
        ("按小时", "hour"), ("每小时", "hour"),
    ]
    # 字段关键字 -> date_field（默认创建时间 created_at）
    date_field_hints = [
        ("创建", "created_at"), ("新增", "created_at"), ("提交", "created_at"), ("录入", "created_at"),
        ("更新", "updated_at"), ("修改", "updated_at"),
        ("解决", "resolved_at"), ("修复", "resolved_at"),
        ("截止", "due_date"), ("到期", "due_date"),
    ]
    chosen_gran = None
    for hint, gran in granularity_hints:
        if hint in full_text:
            chosen_gran = gran
            break
    if chosen_gran is not None:
        chosen_field = "created_at"
        for hint, field in date_field_hints:
            if hint in full_text:
                chosen_field = field
                break
        context["preferred_date_agg"] = {
            "date_field": chosen_field,
            "granularity": chosen_gran,
        }
        # 若没有其它维度分组，时间维度也作为默认分组信号
        if context["preferred_group_by"] is None:
            context["preferred_group_by"] = f"__date__:{chosen_field}:{chosen_gran}"

    return context
