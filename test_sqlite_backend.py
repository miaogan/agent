"""验证 SQLite + mock_remote_api 查询正确性"""
from app.mock_remote_api import query_issue_list, query_issue_detail, _USE_SQLITE, _ensure_backend
from app.schemas import IssueListQuery, IssueStatus

_ensure_backend()
print("USE_SQLITE:", _USE_SQLITE)

# 1. 全量查，第一页
q1 = IssueListQuery(page=1, page_size=20)
r1 = query_issue_list(q1)
print("\n[Test1] 全量查询 total:", r1.total, "page_size:", len(r1.items))
assert r1.total == 1000, f"期望 total=1000, 实际 {r1.total}"
assert len(r1.items) == 20
print("  第一条 id:", r1.items[0].issue_id, "创建人:", r1.items[0].creator, "状态:", r1.items[0].status)

# 2. 翻页验证唯一性
q2 = IssueListQuery(page=2, page_size=20)
r2 = query_issue_list(q2)
ids1 = {i.issue_id for i in r1.items}
ids2 = {i.issue_id for i in r2.items}
assert not (ids1 & ids2), "两页数据不应有重复"
print("\n[Test2] 翻页去重 OK，第二页首条:", r2.items[0].issue_id)

# 3. 按状态过滤
q3 = IssueListQuery(status=[IssueStatus.OPEN, IssueStatus.CLOSED], page_size=1000)
r3 = query_issue_list(q3)
print(f"\n[Test3] 状态 OPEN+CLOSED total={r3.total}")
# OPEN 163 + CLOSED 154 = 317 (seed 报告的值)
assert r3.total > 300
# 验证所有返回状态都在过滤里
for it in r3.items:
    assert it.status in (IssueStatus.OPEN, IssueStatus.CLOSED)

# 4. 按创建人查询
q4 = IssueListQuery(creator="黄涛", page_size=1000)
r4 = query_issue_list(q4)
print(f"\n[Test4] 创建人=黄涛 total={r4.total}")
assert r4.total == 59, f"期望 59，实际 {r4.total}"
for it in r4.items:
    assert it.creator == "黄涛"

# 5. 详情查询
detail = query_issue_detail(r1.items[0].issue_id)
assert detail is not None
print(f"\n[Test5] 详情查询 OK：id={detail.issue_id} title={detail.title[:20]}... tags={detail.tags}")
assert len(detail.tags) >= 0
assert isinstance(detail.comments_count, int)

# 6. 关键词搜索
q6 = IssueListQuery(title_keyword="支付", page_size=1000)
r6 = query_issue_list(q6)
print(f"\n[Test6] 标题+描述包含'支付' total={r6.total}")
for it in r6.items:
    assert ("支付" in it.title) or ("支付" in it.description), f"{it.issue_id} 标题和描述都不含'支付'"

# 7. 按优先级+项目
from app.schemas import IssuePriority
q7 = IssueListQuery(priority=[IssuePriority.CRITICAL, IssuePriority.HIGH], project="支付网关", page_size=1000)
r7 = query_issue_list(q7)
print(f"\n[Test7] 支付网关 + CRITICAL/HIGH priority total={r7.total}")
for it in r7.items:
    assert it.priority in (IssuePriority.CRITICAL, IssuePriority.HIGH)
    assert it.project == "支付网关"

print("\n✅ All SQLite tests passed!")
