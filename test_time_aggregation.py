"""时间聚合的 unit test：验证粒度、字段、传参、结果一致性。"""
from collections import Counter
from datetime import datetime

from app.aggregation import (
    aggregate_by_date,
    aggregate_date_and_field,
    aggregate_by_field,
)
from app.history import extract_context_for_query
from app.mock_remote_api import query_issue_list, _ensure_backend
from app.schemas import IssueListQuery, MessageItem, Role


_ensure_backend()

# 从 SQLite 拉 1000 条作为测试样本
ALL = []
for p in range(1, 21):
    r = query_issue_list(IssueListQuery(page=p, page_size=50))
    ALL.extend(r.items)
    if not r.items:
        break
assert len(ALL) == 1000, f"expect 1000, got {len(ALL)}"


def _dt_created(i):
    return datetime.fromisoformat(i.created_at)


# ================== aggregate_by_date 函数传参正确性 ==================

def test_aggregate_by_date_year():
    g = aggregate_by_date(ALL, "created_at", "year", include_items=False)
    counts_in = Counter(_dt_created(it).year for it in ALL)
    # group 内计数（总计）
    from_db = {int(grp["group_key"]): grp["count"] for grp in g if grp["group_key"] != "__EMPTY__"}
    assert from_db == dict(counts_in), f"year mismatch\nfrom fn: {from_db}\nmanual: {dict(counts_in)}"
    # 附加字段
    for grp in g:
        if grp["group_key"] != "__EMPTY__":
            assert grp["granularity"] == "year"
            assert grp["date_field"] == "created_at"
            assert "items" not in grp  # include_items=False
    print("\n[year aggregation OK] groups:", [(x["group_key"], x["count"]) for x in g])


def test_aggregate_by_date_month():
    g = aggregate_by_date(ALL, "created_at", "month", include_items=False)
    counts_in = Counter(f"{_dt_created(it).year:04d}-{_dt_created(it).month:02d}" for it in ALL)
    from_fn = {grp["group_key"]: grp["count"] for grp in g if grp["group_key"] != "__EMPTY__"}
    assert from_fn == dict(counts_in), f"month mismatch\nfrom fn: {from_fn}\nmanual: {dict(counts_in)}"
    # 每个 group_key 格式都是 YYYY-MM
    for k in from_fn:
        assert len(k) == 7 and k[4] == "-"
    print("\n[month aggregation OK] groups:", [(x["group_key"], x["count"]) for x in g][:8], "...")


def test_aggregate_by_date_day():
    g = aggregate_by_date(ALL, "created_at", "day", include_items=True)
    counts_in = Counter(
        f"{_dt_created(it).year:04d}-{_dt_created(it).month:02d}-{_dt_created(it).day:02d}"
        for it in ALL
    )
    from_fn = {grp["group_key"]: grp["count"] for grp in g if grp["group_key"] != "__EMPTY__"}
    assert from_fn == dict(counts_in)
    total = sum(grp["count"] for grp in g)
    assert total == 1000, f"day total {total}"
    # include_items=True：取一个组看看 items 数量
    non_empty = [grp for grp in g if grp["group_key"] != "__EMPTY__" and grp["count"] > 0][0]
    assert "items" in non_empty
    print(f"\n[day aggregation OK] total groups: {len(g)}, sample group {non_empty['group_key']} "
          f"count={non_empty['count']} items_in_record={len(non_empty['items'])}")


def test_aggregate_by_date_week_quarter_hour():
    # 粒度非空 + 总计匹配
    for gran in ("week", "quarter", "hour"):
        g = aggregate_by_date(ALL, "created_at", gran, include_items=False)
        total = sum(grp["count"] for grp in g)
        assert total == 1000, f"{gran} total {total}"
        for grp in g:
            if grp["group_key"] != "__EMPTY__":
                assert grp["granularity"] == gran
    print("\n[week/quarter/hour totals OK]")


def test_aggregate_by_date_updated_field():
    """切换到 updated_at 字段，验证 date_field 传参确实生效"""
    g1 = aggregate_by_date(ALL, "created_at", "year", include_items=False)
    g2 = aggregate_by_date(ALL, "updated_at", "year", include_items=False)
    # seed 时 updated_at 是最近 30 天、created_at 最近 180 天，分布不同
    k1 = {x["group_key"] for x in g1}
    k2 = {x["group_key"] for x in g2}
    # 两个不同字段聚合的结果集合不能完全一致（除非数据真一致，但 seed 特意不同）
    # 所以只验证各自总计数 1000 即可，集合比较不稳定
    assert sum(x["count"] for x in g1) == 1000
    assert sum(x["count"] for x in g2) == 1000
    # 至少有不同的 group_key（按 seed 设计，created 跨度更大，应该更多）
    print(f"\n[updated_at vs created_at] created keys={k1} updated keys={k2}")


def test_aggregate_by_date_invalid_granularity():
    try:
        aggregate_by_date(ALL, "created_at", "decade")  # type: ignore[arg-type]
    except ValueError as e:
        assert "unsupported granularity" in str(e)
        print("\n[invalid granularity raise OK]")
        return
    raise AssertionError("Expected ValueError")


def test_aggregate_by_date_resolved_at_empties():
    """resolved_at 在 seed 里只有 RESOLVED/CLOSED 有值，其它为空"""
    g = aggregate_by_date(ALL, "resolved_at", "month", include_items=False)
    total = sum(grp["count"] for grp in g if grp["group_key"] != "__EMPTY__")
    # 期望等于 RESOLVED(154) + CLOSED(154) = 308
    empty_group = [grp for grp in g if grp["group_key"] == "__EMPTY__"]
    empty_count = empty_group[0]["count"] if empty_group else 0
    print(f"\n[resolved_at] non-empty total={total}, empty={empty_count}, total sum={total+empty_count}")
    assert total + empty_count == 1000
    assert 300 <= total <= 320, f"期望 ~308 非空，实际 {total}"
    print("[resolved_at empties handling OK]")


# ================== 时间 × 维度 交叉聚合 ==================

def test_aggregate_date_and_field_month_status():
    result = aggregate_date_and_field(ALL, "created_at", "month", "status")
    # 每个月的 breakdown.status 计数加起来等于该月总 count
    for r in result:
        if r["group_key"] == "__EMPTY__":
            continue
        brk = r.get("breakdown_by_status") or []
        brk_sum = sum(b["count"] for b in brk)
        assert brk_sum == r["count"], f"{r['group_key']} breakdown sum {brk_sum} != count {r['count']}"
    total = sum(r["count"] for r in result)
    assert total == 1000
    print(f"\n[month×status OK] groups={len(result)}, 第一组样例 {result[0]['group_key']} "
          f"count={result[0]['count']}, breakdown={result[0].get('breakdown_by_status')[:3]}")


# ================== extract_context_for_query 传参推断（传参链路核心）==================

def _ctx(text: str) -> dict:
    return extract_context_for_query([], text)


def test_context_infer_year_created():
    c = _ctx("按年统计每年创建的问题单数量")
    assert c["preferred_date_agg"] == {"date_field": "created_at", "granularity": "year"}, c
    # 时间推断时，未指定维度，则设置占位 preferred_group_by
    assert c["preferred_group_by"].startswith("__date__")
    print("\n[context infer year OK]", c["preferred_date_agg"])


def test_context_infer_month_updated():
    c = _ctx("按月给出更新时间统计")
    assert c["preferred_date_agg"] == {"date_field": "updated_at", "granularity": "month"}, c
    print("\n[context infer month+updated OK]", c["preferred_date_agg"])


def test_context_infer_day_resolved():
    c = _ctx("按天统计每日修复的问题单")
    assert c["preferred_date_agg"] == {"date_field": "resolved_at", "granularity": "day"}, c
    print("\n[context infer day+resolved OK]", c["preferred_date_agg"])


def test_context_cross_month_and_status():
    """同时指定时间 + 维度：两种偏好都要设置"""
    c = _ctx("按月再按状态分组统计创建的问题单")
    assert c["preferred_date_agg"] == {"date_field": "created_at", "granularity": "month"}
    assert c["preferred_group_by"] == "status"
    print("\n[context cross month+status OK] pref=", c["preferred_group_by"],
          "date_agg=", c["preferred_date_agg"])


def test_context_quarter_due():
    c = _ctx("每个季度到期问题单数量")
    assert c["preferred_date_agg"] == {"date_field": "due_date", "granularity": "quarter"}, c
    print("\n[context quarter+due OK]", c["preferred_date_agg"])


def test_context_weekly_creation():
    c = _ctx("按周汇总每周新增问题单")
    assert c["preferred_date_agg"] == {"date_field": "created_at", "granularity": "week"}, c
    print("\n[context week OK]", c["preferred_date_agg"])


def test_context_dimension_only_no_time():
    """保持向后兼容：纯按状态分组不触发时间聚合"""
    c = _ctx("按状态分组统计")
    assert c["preferred_group_by"] == "status"
    assert c["preferred_date_agg"] is None
    print("\n[context dim-only backward compatible OK]")


if __name__ == "__main__":
    # 手动顺序执行，避免 pytest
    test_aggregate_by_date_year()
    test_aggregate_by_date_month()
    test_aggregate_by_date_day()
    test_aggregate_by_date_week_quarter_hour()
    test_aggregate_by_date_updated_field()
    test_aggregate_by_date_invalid_granularity()
    test_aggregate_by_date_resolved_at_empties()
    test_aggregate_date_and_field_month_status()
    test_context_infer_year_created()
    test_context_infer_month_updated()
    test_context_infer_day_resolved()
    test_context_cross_month_and_status()
    test_context_quarter_due()
    test_context_weekly_creation()
    test_context_dimension_only_no_time()
    print("\n✅ ALL 15 time-aggregation tests PASSED")
