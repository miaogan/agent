from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from app.schemas import AggregationResult, IssueDetail

Granularity = Literal["year", "quarter", "month", "week", "day", "hour"]
GRANULARITIES: tuple[str, ...] = ("year", "quarter", "month", "week", "day", "hour")

# ≈ 40k tokens。聚合结果超过此字符数时自动删除 items 明细等大字段做降级，避免 10000+ 条数据撑爆 LLM context。
# 10000 条按小时聚合大约 1000 组 × 每组 ~80 chars (无 items) ≈ 80k chars，此阈值刚好容纳。
MAX_AGG_JSON_CHARS = 80000


def _json_len(o: Any) -> int:
    try:
        return len(json.dumps(o, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        return 2**31


def _strip_items_deep(o: Any) -> Any:
    if isinstance(o, dict):
        out: dict[str, Any] = {}
        for k, v in o.items():
            if k == "items":
                count = len(v) if isinstance(v, list) else None
                note = "removed items to reduce context size"
                if count is not None:
                    note = f"removed {count} items to reduce context size"
                out["items_note"] = note
                continue
            out[k] = _strip_items_deep(v)
        return out
    if isinstance(o, list):
        return [_strip_items_deep(x) for x in o]
    return o


def _sum_group_counts(groups: list[Any]) -> int:
    total = 0
    for g in groups:
        try:
            total += int(g.get("count", 0) if isinstance(g, dict) else 0)
        except Exception:  # noqa: BLE001
            pass
    return total


def _cap_aggregation_output(result: Any, max_chars: int = MAX_AGG_JSON_CHARS) -> Any:
    """兜底：聚合结果 JSON 太大时先深度删除 items 明细，若仍超长则截断组列表。"""
    if _json_len(result) <= max_chars:
        return result
    stripped = _strip_items_deep(result)
    if _json_len(stripped) <= max_chars:
        return stripped
    if isinstance(stripped, list):
        kept: list[Any] = []
        dropped: list[Any] = []
        for g in stripped:
            if _json_len(kept + [g]) <= max_chars:
                kept.append(g)
            else:
                dropped.append(g)
        if dropped:
            dropped_records = _sum_group_counts(dropped)
            kept.append({
                "group_key": "__TRUNCATED__",
                "count": dropped_records,
                "groups_dropped": len(dropped),
                "note": (f"{len(dropped)} groups ({dropped_records} records) trimmed "
                         f"to fit context size limit ({max_chars} chars)"),
            })
        return kept
    return stripped


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
    include_items: bool = False,
    max_items_per_group: int = 20,
    max_agg_chars: int = MAX_AGG_JSON_CHARS,
) -> list[dict[str, Any]]:
    """按指定字段分组聚合，远程接口不支持此能力，需在应用层实现。
    常用字段: status/priority/category/severity/creator/assignee/project/module/environment/fault_component/version

    注意：include_items 默认 False，避免明细把 LLM context 撑爆。若确需样本可显式传 True，
    此时 max_items_per_group 默认限制为 20（仍会经过 _cap_aggregation_output 全局兜底强制裁剪超长结果，
    可通过 max_agg_chars 上调阈值，例如 unit test 中需要断言 items 时传大值）。
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
    return _cap_aggregation_output(result, max_chars=max_agg_chars)


def aggregate_two_level(
    items: list[Any],
    field1: str,
    field2: str,
) -> list[dict[str, Any]]:
    """双层分组（如先按项目，再按状态），客户端聚合典型场景。"""
    first = aggregate_by_field(items, field1, include_items=True,
                               max_items_per_group=len(items),
                               max_agg_chars=10**9)  # 二级聚合内部调用，先不做 cap，由最终返回统一处理
    result = []
    for g in first:
        nested = []
        for x in g.get("items") or []:
            nested.append(x if isinstance(x, dict) else _as_dict(x))
        sub = aggregate_by_field(nested, field2, include_items=False)
        result.append({
            "group_key": g["group_key"],
            "count": g["count"],
            "sub_groups": sub,
        })
    return _cap_aggregation_output(result)


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

    result = {
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
    return _cap_aggregation_output(result, max_chars=MAX_AGG_JSON_CHARS)


# ------------------------- 时间维度聚合（按年/季/月/周/日/小时） -------------------------

def _parse_dt(v: Any) -> Any | None:
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
    include_items: bool = False,
    max_items_per_group: int = 20,
    max_agg_chars: int = MAX_AGG_JSON_CHARS,
) -> list[dict[str, Any]]:
    """按时间粒度聚合（远程接口不支持按 date_part 分组，需本地计算）。

    Args:
        items: 问题单列表（dict 或 IssueDetail 对象均可）
        date_field: 用于聚合的时间字段，如 created_at / updated_at / resolved_at / due_date
        granularity: year / quarter / month / week / day / hour
        include_items: 是否将明细 records 塞进每组 items。默认 False，避免 10000+ 条按日分组时 180 天×20 条仍可能超长（有 _cap_aggregation_output 兜底）
        max_items_per_group: 每组最多保留多少条明细（仅当 include_items=True 时生效）
        max_agg_chars: 结果 JSON 字符阈值，超过会自动降级裁剪（unit test 可上调）
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
    return _cap_aggregation_output(result, max_chars=max_agg_chars)


def aggregate_date_and_field(
    items: list[Any],
    date_field: str = "created_at",
    granularity: Granularity = "month",
    second_field: str = "status",
    max_agg_chars: int = MAX_AGG_JSON_CHARS,
) -> list[dict[str, Any]]:
    """时间 + 维度的双层交叉聚合（例如：每月按状态分组）。"""
    n = len(items) if isinstance(items, list) else 1000000
    first = aggregate_by_date(items, date_field=date_field, granularity=granularity,
                              include_items=True, max_items_per_group=n,
                              max_agg_chars=10**9)  # 中间态不 cap，交给最终 cap
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
    return _cap_aggregation_output(result, max_chars=max_agg_chars)


AGGREGATE_HELP = """
可用的客户端聚合操作（远程接口无法实现，需本地计算）：
1. aggregate_by_field(items, field, include_items=False) - 按单字段分组计数，可选字段：
   status/priority/category/severity/creator/assignee/project/module/environment/fault_component/version
2. aggregate_two_level(items, field1, field2) - 双层分组，如先按project再按status
3. stat_summary(items) - 综合统计概览：总数、各维度分布、评论/投票/关注数求和及均值
4. aggregate_by_date(items, date_field, granularity, include_items=False) - 按时间粒度聚合（远程接口无 date_part 能力）：
   date_field ∈ {created_at, updated_at, resolved_at, due_date}
   granularity ∈ {year, quarter, month, week, day, hour}
   例：按创建年 aggregate_by_date(items, 'created_at', 'year')
       按创建月 aggregate_by_date(items, 'created_at', 'month')
       按更新日 aggregate_by_date(items, 'updated_at', 'day')
5. aggregate_date_and_field(items, date_field, granularity, second_field) - 时间 × 维度交叉：
   例：每月按状态 aggregate_date_and_field(items, 'created_at', 'month', 'status')
所有聚合结果最终都会经过 MAX_AGG_JSON_CHARS≈20000 chars 兜底裁剪（删除 items 明细 / 截断尾部 groups），避免 10000+ 条数据把 LLM context 撑爆。
"""
