"""测试 10000 条数据下：全量查询 / 分组查询 会不会导致上下文超长。

分两步：
1) 静态估算：直接调用 mock_remote_api + aggregation 函数，统计返回 JSON 字符数，token≈chars/2
2) E2E 走完整 agent graph（用 mock LLM 不真正推理），看最终 prompt 里各消息累计字符与 token 估算
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from app.aggregation import (
    aggregate_by_date,
    aggregate_by_field,
    aggregate_date_and_field,
    stat_summary,
)
from app.history import extract_context_for_query
from app.schemas import IssueListQuery

# 懒加载，触发 _ensure_backend 打印
from app.mock_remote_api import query_issue_list


def tokens(c: int) -> int:
    return max(1, math.ceil(c / 2))


def fetch_all_10k():
    """一次拉 10000 条（page_size=10000）"""
    q = IssueListQuery(page=1, page_size=10000)
    resp = query_issue_list(q)
    return resp.model_dump(mode="json")


def build_tool_prompt_payload(content: dict) -> dict:
    """完全复刻 agent_core._execute_tool_calls 中 search_issue_list 的摘要逻辑"""
    items = content.get("items") or []
    sample = []
    for it in items[:5]:
        if isinstance(it, dict):
            sample.append({
                "issue_id": it.get("issue_id"),
                "title": str(it.get("title") or "")[:40],
                "status": it.get("status"),
                "priority": it.get("priority"),
                "project": it.get("project"),
                "assignee": it.get("assignee"),
            })
    return {
        "_note": "(仅为prompt摘要, SSE事件中已推全量结果)",
        "tool": "search_issue_list",
        "total": content.get("total"),
        "page": content.get("page"),
        "page_size": content.get("page_size"),
        "returned_items_count": len(items),
        "first_5_items_sample": sample,
    }


def estimate_context_chars_of_agg(agg_result: Any, ctx_label: str) -> tuple[int, int]:
    """聚合结果会作为 HumanMessage content 注入：前缀 + JSON"""
    prefix = "[本地聚合结果(远程接口不支持分组/统计，请基于此结果给用户结论，用markdown表格)]\n"
    json_str = json.dumps(agg_result, ensure_ascii=False, indent=2)
    total_chars = len(prefix) + len(json_str)
    print(f"  [{ctx_label}] 聚合HumanMessage chars={len(json_str)} (JSON) + {len(prefix)} 前缀 = {total_chars}  ≈ {tokens(total_chars)} tokens")
    # 同时看如果把 items 字段全删掉（只保留 count/group_key），节省多少
    trimmed = _strip_items(agg_result)
    trimmed_str = json.dumps(trimmed, ensure_ascii=False, indent=2)
    print(f"    → 若去掉items明细 chars={len(trimmed_str)}  ≈ {tokens(len(trimmed_str))} tokens  (节省 {100 - round(100*len(trimmed_str)/max(1,len(json_str)),1)}%)")
    return total_chars, tokens(total_chars)


def _strip_items(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _strip_items(v) for k, v in o.items() if k != "items"}
    if isinstance(o, list):
        return [_strip_items(x) for x in o]
    return o


def static_estimate():
    print("=" * 80)
    print("10000 条问题单 —— 静态估算上下文长度")
    print("=" * 80)
    resp = fetch_all_10k()
    all_items = resp["items"]
    print(f"\n[search_issue_list page_size=10000] total={resp['total']} returned_items={len(all_items)}")
    # 1. 工具 ToolMessage 摘要（原本就有做）
    payload = build_tool_prompt_payload(resp)
    payload_str = json.dumps(payload, ensure_ascii=False)
    print(f"\n👉 全量查询 ToolMessage 摘要 chars={len(payload_str)}  ≈ {tokens(len(payload_str))} tokens")
    # 对比：如果没做摘要、直接塞全 items
    full_str = json.dumps(resp, ensure_ascii=False)
    print(f"   ❌ （若不做摘要直接塞全量 response 会是 chars={len(full_str)}  ≈ {tokens(len(full_str))} tokens → {tokens(len(full_str))/1000:.1f}k tokens，肯定爆）")
    print(f"   ✅ 摘要机制节省 {100 - round(100*len(payload_str)/len(full_str), 1)}%，已安全")

    # 2. 分组场景（聚合后注入 HumanMessage）
    print("\n--- 分组查询 · 聚合结果注入 Prompt 的长度 ---")
    cases = [
        ("按状态分组(默认 include_items=True, max=100)",
         lambda: aggregate_by_field(all_items, "status")),
        ("按创建人分组(20 人，默认 include_items=True)",
         lambda: aggregate_by_field(all_items, "creator")),
        ("按创建月聚合(include_items=True, max=100)",
         lambda: aggregate_by_date(all_items, "created_at", "month")),
        ("按创建日聚合(180+ 天，include_items=True, max=100)",
         lambda: aggregate_by_date(all_items, "created_at", "day")),
        ("按创建年×状态 交叉聚合",
         lambda: aggregate_date_and_field(all_items, "created_at", "year", "status")),
        ("按创建月×状态 交叉聚合",
         lambda: aggregate_date_and_field(all_items, "created_at", "month", "status")),
        ("按创建日×状态 交叉聚合(180+天)",
         lambda: aggregate_date_and_field(all_items, "created_at", "day", "status")),
        ("stat_summary 综合统计概览(默认不带明细)",
         lambda: stat_summary(all_items)),
        ("aggregate_by_field status include_items=False",
         lambda: aggregate_by_field(all_items, "status", include_items=False)),
        ("aggregate_by_date day include_items=False",
         lambda: aggregate_by_date(all_items, "created_at", "day", include_items=False)),
    ]
    for label, fn in cases:
        try:
            r = fn()
            estimate_context_chars_of_agg(r, label)
        except Exception as e:
            print(f"  [{label}] 失败: {e}")


async def e2e_agent_flow():
    """走完整 agent graph（替换 LLM 为 mock），统计最终传给 LLM 的 messages 总字符/token 估算。"""
    print("\n" + "=" * 80)
    print("E2E Agent Flow（mock LLM，不调用本地模型）")
    print("=" * 80)
    from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, HumanMessage, ToolMessage
    from app.agent_core import GRAPH, _LLM_FACTORY_OVERRIDE, MAX_HISTORY_ROUNDS, _has_tool_call
    from app.history import trim_history
    from app.schemas import MessageItem, Role

    # 捕获 node_agent 收到的 messages
    captured = {"invoke_msgs": [], "stream_msgs": []}

    class FakeLLM:
        def __init__(self, streaming: bool = False):
            self.streaming = streaming
            self._tools = None

        def bind_tools(self, tools):
            self._tools = tools
            return self

        def invoke(self, messages):
            captured["invoke_msgs"].append(list(messages))
            # 行为：第一轮 -> 调 search_issue_list；收到工具结果 -> 直接输出一句话（带 tool_calls 的 AIMessage 或者纯回答）
            # 看有没有 ToolMessage，如果有就是第二轮，直接给回答
            has_tool = any(isinstance(m, ToolMessage) for m in messages)
            if not has_tool:
                # 第一轮：返回 AIMessage 带 tool_call search_issue_list(page_size=10000)
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "search_issue_list",
                        "args": {"page": 1, "page_size": 10000},
                        "id": "call-1",
                        "type": "tool_call",
                    }],
                )
            # 第二轮：有聚合结果就直接回答
            return AIMessage(content="根据聚合分析结果，已给出各分组的统计结论。")

        def stream(self, messages):
            captured["stream_msgs"].append(list(messages))
            resp_text = "根据聚合分析结果，已给出各分组的统计结论。"
            class Chunk:
                def __init__(self, c):
                    self.content = c
            for ch in resp_text:
                yield Chunk(ch)

    prev = _LLM_FACTORY_OVERRIDE
    try:
        _LLM_FACTORY_OVERRIDE = lambda **kw: FakeLLM(**kw)

        scenarios = [
            ("全量查询后按状态分组（10000条）", "帮我把所有问题单按状态分组统计一下"),
            ("按日聚合每天创建的问题单数量（10000条）", "按日统计每天创建的问题单数量"),
            ("按状态分组 + 按月交叉聚合", "按创建月统计每月创建量并按状态分组"),
        ]
        for title, user_input in scenarios:
            print(f"\n--- 场景：{title} ---")
            print(f"  用户输入：{user_input}")
            items = [MessageItem(role=Role.USER, content=user_input)]
            final_state = None
            loop = asyncio.get_event_loop()
            it = iter(GRAPH.stream({"round_history": items}, stream_mode="values"))

            def _next():
                nonlocal final_state
                try:
                    s = next(it)
                    final_state = s
                    return True
                except StopIteration:
                    return False

            while True:
                ok = await loop.run_in_executor(None, _next)
                if not ok:
                    break

            messages: list[BaseMessage] = final_state["messages"]
            # 统计所有 invoke 阶段传给 LLM 的 messages 总长（最关键是第二轮包含了聚合结果的 HumanMessage）
            total_chars_all_invocations = 0
            for idx, ms in enumerate(captured["invoke_msgs"]):
                chars = sum(len(m.content or "") + len((" ".join(str(tc) for tc in (getattr(m, "tool_calls", None) or [])))) for m in ms)
                # HumanMessage/SystemMessage 计算 content；ToolMessage 也是 content
                # 更准确：把 messages 转成文本段
                ser = _serialize_messages(ms)
                chars = len(ser)
                total_chars_all_invocations += chars
                print(f"  LLM 调用 #{idx+1}：messages chars={chars}  ≈ {tokens(chars)} tokens  ({len(ms)} messages)")
                if idx + 1 == len(captured["invoke_msgs"]):
                    # 打最后一次 messages 的主要长度来源
                    _summarize_message_sizes(ms)
            captured["invoke_msgs"].clear()
            captured["stream_msgs"].clear()
            print(f"  合计 LLM 调用 chars≈{total_chars_all_invocations}  ≈ {tokens(total_chars_all_invocations)} tokens")
            # 风险提示（按 16k context 为例）
            tk = tokens(total_chars_all_invocations)
            if tk > 8000:
                print(f"  ⚠️  超 8k tokens，可能超限")
            elif tk > 4000:
                print(f"  ⚠️  超 4k tokens，较紧张")
            else:
                print(f"  ✅  < 4k tokens，安全")
    finally:
        _LLM_FACTORY_OVERRIDE = prev


def _serialize_messages(ms: list) -> str:
    parts = []
    for m in ms:
        role = getattr(m, "type", "msg")
        content = getattr(m, "content", "") or ""
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        tcs = getattr(m, "tool_calls", None) or []
        extra = ""
        if tcs:
            extra = " tool_calls=" + json.dumps(tcs, ensure_ascii=False)
        parts.append(f"<{role}>{content}{extra}</{role}>")
    return "\n".join(parts)


def _summarize_message_sizes(ms: list):
    print("    各消息长度 Top：")
    ranked = []
    for i, m in enumerate(ms):
        c = len(getattr(m, "content", "") or "")
        ranked.append((c, i, type(m).__name__, (getattr(m, "content", "") or "")[:100].replace("\n", "\\n")))
    ranked.sort(reverse=True)
    for c, i, name, snippet in ranked[:5]:
        print(f"      #{i} {name:<18} chars={c:<7} ≈ {tokens(c):<5} tokens  head={snippet!r}")


if __name__ == "__main__":
    static_estimate()
    asyncio.run(e2e_agent_flow())
