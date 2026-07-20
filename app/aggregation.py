from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from app.schemas import AggregationResult, IssueDetail

Granularity = Literal["year", "quarter", "month", "week", "day", "hour"]
GRANULARITIES: tuple[str, ...] = ("year", "quarter", "month", "week", "day", "hour")


def _as_dict(it: Any) -> dict[str, Any]:
    if isinstance(it, dict):
        return it
    if isinstance(it, IssueDetail):
        return it.model_dump(mode="json")
    try:
        return dict(it)
    except Exception:  # noqa: BLE001
        raise TypeError(f"aggregate: item 必须是 dict 或 IssueDetail，实际 {type(it).__name__}")


def _get(it: Any, field: str) -> Any:
    if isinstance(it, dict):
        return it.get(field)
    # IssueDetail/Pydantic 对象优先 getattr，兼容 Enum
    try:
        val = getattr(it, field)
    except AttributeError:
        return None
    # Enum 转字符串值（如 IssueStatus.OPEN -> "OPEN"）
    if hasattr(val, "value"):
        try:
            return val.value
        except Exception:  # noqa: BLE001
            pass
    return val


def aggregate_by_field(
    items: list[Any],
    field: str,
    include_items: bool = True,
    max_items_per_group: int = 100,
) -> list[dict[str, Any]]:
    """按指定字段分组聚合，远程接口不支持此能力，需在应用层实现。
    常用字段: status/priority/category/severity/creator/assignee/project/module/environment/fault_component/version
    """
    groups: dict[str, list[Any]] = defaultdict(list)
    for it in items:
        key = _get(it, field)
        if key is None:
            key = "__EMPTY__"
        if isinstance(key, list):
            for sub in key:
                groups[str(sub)].append(it)
        else:
            groups[str(key)].append(it)

    result = []
    for key, group_items in sorted(groups.items(), key=lambda x: -len(x[1])):
        entry: dict[str, Any] = {
            "group_key": key,
            "count": len(group_items),
        }
        if include_items:
            safe_items: list[dict[str, Any]] = [_as_dict(x) for x in group_items[:max_items_per_group]]
            entry["items"] = safe_items
        result.append(entry)
    return result


def aggregate_two_level(
    items: list[Any],
    field1: str,
    field2: str,
) -> list[dict[str, Any]]:
    """双层分组（如先按项目，再按状态），客户端聚合典型场景。"""
    first = aggregate_by_field(items, field1, include_items=True, max_items_per_group=10000)
    result = []
    for g in first:
        nested = []
        for x in g["items"]:
            nested.append(x if isinstance(x, dict) else _as_dict(x))
        sub = aggregate_by_field(nested, field2, include_items=False)
        result.append({
            "group_key": g["group_key"],
            "count": g["count"],
            "sub_groups": sub,
        })
    return result


def stat_summary(
    items: list[Any],
) -> dict[str, Any]:
    """对查询结果做统计概览（计数、数值型求和/平均等）。"""
    total = len(items)
    by_status = aggregate_by_field(items, "status", include_items=False)
    by_priority = aggregate_by_field(items, "priority", include_items=False)
    by_severity = aggregate_by_field(items, "severity", include_items=False)
    by_project = aggregate_by_field(items, "project", include_items=False)
    by_assignee = aggregate_by_field(items, "assignee", include_items=False)

    def _sum_field(name: str) -> int:
        s = 0
        for it in items:
            v = _get(it, name)
            try:
                s += int(v or 0)
            except Exception:  # noqa: BLE001
                pass
        return s

    total_comments = _sum_field("comments_count")
    total_votes = _sum_field("votes_count")
    total_watchers = _sum_field("watchers_count")

    return {
        "total": total,
        "by_status": by_status,
        "by_priority": by_priority,
        "by_severity": by_severity,
        "by_project": by_project,
        "by_assignee": by_assignee,
        "sum_comments_count": total_comments,
        "sum_votes_count": total_votes,
        "sum_watchers_count": total_watchers,
        "avg_comments_per_issue": round(total_comments / total, 2) if total else 0,
    }


# ------------------------- 时间维度聚合（按年/季/月/周/日/小时） -------------------------

def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    # 常见 ISO 格式
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    # YYYY-MM-DD HH:MM:SS / YYYY-MM-DD
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _truncate_to_granularity(dt: datetime, granularity: Granularity) -> str:
    if granularity == "year":
        return f"{dt.year:04d}"
    if granularity == "quarter":
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year:04d}-Q{q}"
    if granularity == "month":
        return f"{dt.year:04d}-{dt.month:02d}"
    if granularity == "week":
        iso = dt.isocalendar()
        return f"{iso[0]:04d}-W{iso[1]:02d}"
    if granularity == "day":
        return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
    if granularity == "hour":
        return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:00"
    return dt.isoformat()


def aggregate_by_date(
    items: list[Any],
    date_field: str = "created_at",
    granularity: Granularity = "month",
    include_items: bool = True,
    max_items_per_group: int = 100,
) -> list[dict[str, Any]]:
    """按时间粒度聚合（远程接口不支持按 date_part 分组，需本地计算）。

    Args:
        items: 问题单列表（dict 或 IssueDetail 对象均可）
        date_field: 用于聚合的时间字段，如 created_at / updated_at / resolved_at / due_date
        granularity: year / quarter / month / week / day / hour
        include_items: 是否将明细 records 塞进每组 items
        max_items_per_group: 每组最多保留多少条明细
    """
    if granularity not in GRANULARITIES:
        raise ValueError(
            f"unsupported granularity={granularity!r}, must be one of {list(GRANULARITIES)}"
        )
    if not date_field:
        raise ValueError("date_field is required")

    groups: dict[str, list[Any]] = defaultdict(list)
    empties: list[Any] = []
    for it in items:
        raw = _get(it, date_field)
        dt = _parse_dt(raw)
        if dt is None:
            empties.append(it)
            continue
        key = _truncate_to_granularity(dt, granularity)
        groups[key].append(it)

    sorted_keys = sorted(groups.keys())
    result: list[dict[str, Any]] = []
    for key in sorted_keys:
        group_items = groups[key]
        entry: dict[str, Any] = {
            "group_key": key,
            "granularity": granularity,
            "date_field": date_field,
            "count": len(group_items),
        }
        if include_items:
            safe: list[dict[str, Any]] = [_as_dict(x) for x in group_items[:max_items_per_group]]
            entry["items"] = safe
        result.append(entry)
    if empties:
        entry = {
            "group_key": "__EMPTY__",
            "granularity": granularity,
            "date_field": date_field,
            "count": len(empties),
        }
        if include_items:
            entry["items"] = [_as_dict(x) for x in empties[:max_items_per_group]]
        result.append(entry)
    return result


def aggregate_date_and_field(
    items: list[Any],
    date_field: str = "created_at",
    granularity: Granularity = "month",
    second_field: str = "status",
) -> list[dict[str, Any]]:
    """时间 + 维度的双层交叉聚合（例如：每月按状态分组）。"""
    first = aggregate_by_date(items, date_field=date_field, granularity=granularity,
                              include_items=True, max_items_per_group=1000000)
    result: list[dict[str, Any]] = []
    for g in first:
        nested = g.get("items") or []
        sub = aggregate_by_field(nested, second_field, include_items=False)
        result.append({
            "group_key": g["group_key"],
            "granularity": granularity,
            "date_field": date_field,
            "count": g["count"],
            "breakdown_by_" + second_field: sub,
        })
    return result


AGGREGATE_HELP = """
可用的客户端聚合操作（远程接口无法实现，需本地计算）：
1. aggregate_by_field(items, field) - 按单字段分组计数，可选字段：
   status/priority/category/severity/creator/assignee/project/module/environment/fault_component/version
2. aggregate_two_level(items, field1, field2) - 双层分组，如先按project再按status
3. stat_summary(items) - 综合统计概览：总数、各维度分布、评论/投票/关注数求和及均值
4. aggregate_by_date(items, date_field, granularity) - 按时间粒度聚合（远程接口无 date_part 能力）：
   date_field ∈ {created_at, updated_at, resolved_at, due_date}
   granularity ∈ {year, quarter, month, week, day, hour}
   例：按创建年 aggregate_by_date(items, 'created_at', 'year')
       按创建月 aggregate_by_date(items, 'created_at', 'month')
       按更新日 aggregate_by_date(items, 'updated_at', 'day')
5. aggregate_date_and_field(items, date_field, granularity, second_field) - 时间 × 维度交叉：
   例：每月按状态 aggregate_date_and_field(items, 'created_at', 'month', 'status')
"""
