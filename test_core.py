import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.aggregation import aggregate_by_field, aggregate_two_level, stat_summary
from app.history import extract_context_for_query, trim_history
from app.mock_remote_api import query_issue_detail, query_issue_list
from app.schemas import IssueListQuery, MessageItem, Role
from app.tools import get_issue_detail, search_issue_list

print("=== 1. 列表查询工具(19参数) ===")
q = IssueListQuery(status=["OPEN", "IN_PROGRESS"], page_size=5)
res = query_issue_list(q)
print(f"命中 {res.total} 条, 返回 {len(res.items)} 条")
print("单条字段数:", len(res.items[0].model_dump()))
print("字段列表:", list(res.items[0].model_dump().keys())[:10], "...")

print("\n=== 2. 详情查询工具(30字段) ===")
one = res.items[0]
detail = query_issue_detail(one.issue_id)
assert detail is not None
print(f"{detail.issue_id}: status={detail.status}, creator={detail.creator}, project={detail.project}, fault_cause={detail.fault_cause[:20]}...")
print("总字段数:", len(detail.model_dump()))

print("\n=== 3. 工具层调用 ===")
tool_res = search_issue_list.invoke({"status": ["OPEN"], "page_size": 3})
print("search_issue_list 总数:", tool_res["total"], "返回数:", len(tool_res["items"]))
d2 = get_issue_detail.invoke({"issue_id": one.issue_id})
print("get_issue_detail 字段数:", len(d2))

print("\n=== 4. 本地聚合(远程接口不支持) ===")
all_items = [x.model_dump() for x in query_issue_list(IssueListQuery(page_size=200)).items]
by_status = aggregate_by_field(all_items, "status")
print("按状态分组:")
for g in by_status:
    print(f"  {g['group_key']:<12} {g['count']}")

by_project_status = aggregate_two_level(all_items, "project", "status")
print("\n按项目×状态双层分组(前2个项目):")
for g in by_project_status[:2]:
    sub = ", ".join(f"{s['group_key']}:{s['count']}" for s in g["sub_groups"])
    print(f"  {g['group_key']}  total={g['count']}  sub=[{sub}]")

stats = stat_summary(all_items)
print("\n综合统计: total=", stats["total"], "各状态counts=", [(x["group_key"], x["count"]) for x in stats["by_status"]])

print("\n=== 5. 历史截断与上下文提取 ===")
hist = []
for i in range(6):
    hist.append(MessageItem(role=Role.USER, content=f"第{i+1}轮问题"))
    hist.append(MessageItem(role=Role.ASSISTANT, content=f"第{i+1}轮回答"))
trimmed = trim_history(hist, 3)
user_rounds = sum(1 for m in trimmed if m.role == Role.USER)
print(f"原history {len(hist)}条, 截断后 {len(trimmed)}条, 用户轮数={user_rounds} (应为3)")
ctx = extract_context_for_query(
    trimmed, "刚才那些按状态分组统计一下张伟处理的"
)
print("上下文提取:")
print(json.dumps(ctx, ensure_ascii=False, indent=2))

print("\n=== ALL CHECKS PASSED ===")
