"""端到端测试：验证用户自然语言 -> 工具调用 -> 时间聚合 -> 注入 LLM。

用 StubLLM 模拟 LLM 决策（不依赖 LM Studio），完整走通：
run_agent_stream -> node_prepare(推断 date_agg) -> node_agent(生成 tool call)
-> node_tools(执行 search_issue_list) -> _maybe_aggregate(时间聚合)
-> 注入聚合 user msg -> node_agent(输出最终答案) -> history 事件
"""
import asyncio
import json
import re
import uuid
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agent_core import TOOLS, run_agent_stream


CASES = {
    "按月统计创建的问题单数量": {
        "tool_name": "search_issue_list",
        "tool_args": {"page": 1, "page_size": 200},
        "agg_type_keyword": "date_month",  # 聚合类型里应该包含这个
        "final_answer": "根据按月聚合统计结果，各月创建的问题单数量如上。",
    },
    "按季度统计每个季度到期的问题单": {
        "tool_name": "search_issue_list",
        "tool_args": {"page": 1, "page_size": 200},
        "agg_type_keyword": "date_quarter",
        "final_answer": "根据按季度到期问题单的聚合结果，各季度数量如上。",
    },
    "按日统计每天更新的问题单数量": {
        "tool_name": "search_issue_list",
        "tool_args": {"page": 1, "page_size": 200},
        "agg_type_keyword": "date_day",
        "final_answer": "根据按日更新聚合统计，每日更新问题单数量如上。",
    },
    "按年统计创建量，再按状态分组": {
        "tool_name": "search_issue_list",
        "tool_args": {"page": 1, "page_size": 200},
        "agg_type_keyword": "date_by_year_and_status",  # 交叉聚合类型
        "final_answer": "根据按年×状态交叉聚合，结果如上。",
    },
}


class StubLLM:
    def __init__(self, user_input: str):
        self.ui = user_input
        self.tc_done = False
        self._bind_tools_called = False

    def bind_tools(self, tools):
        self._bind_tools_called = True
        return self

    def invoke(self, messages, config=None, **kwargs):
        has_tool = any(m.__class__.__name__ == "ToolMessage" for m in messages)
        cfg = CASES[self.ui]
        if not has_tool and not self.tc_done:
            self.tc_done = True
            tc_id = "call_" + uuid.uuid4().hex[:8]
            tc = {"name": cfg["tool_name"], "args": dict(cfg["tool_args"]), "id": tc_id, "type": "tool_call"}
            return AIMessage(content=f"调用{cfg['tool_name']}。", tool_calls=[tc])
        return AIMessage(content=cfg["final_answer"])

    def stream(self, messages, config=None, **kwargs):
        full = self.invoke(messages, config=config, **kwargs)
        tcs = getattr(full, "tool_calls", None) or []
        if tcs:
            for idx, tc in enumerate(tcs):
                yield AIMessageChunk(
                    content="",
                    tool_call_chunks=[{
                        "name": tc["name"], "args": None, "id": tc["id"], "index": idx,
                    }],
                )
                yield AIMessageChunk(
                    content="",
                    tool_call_chunks=[{
                        "name": None,
                        "args": json.dumps(tc["args"], ensure_ascii=False),
                        "id": None, "index": idx,
                    }],
                )
            yield AIMessageChunk(content=full.content or "")
        else:
            c = full.content or ""
            for i in range(0, len(c), 3):
                yield AIMessageChunk(content=c[i:i+3])


def _factory(user_input: str):
    stub = StubLLM(user_input)
    stub.bind_tools(TOOLS)

    def make(*, streaming: bool = True):
        return stub

    return make


async def run_case(user_input: str):
    print(f"\n==== [E2E] {user_input} ====")
    cfg = CASES[user_input]
    factory = _factory(user_input)

    tc_events: list = []
    tr_events: list = []
    chunks: list = []
    history = None
    async for ev in run_agent_stream(user_input=user_input, history=[], llm_factory=factory):
        t = ev.get("type")
        if t == "tool_call":
            tc_events.append(ev)
            print(f"  ✅ tool_call {ev['name']} args={json.dumps(ev.get('arguments'), ensure_ascii=False)[:80]}")
        elif t == "tool_result":
            tr_events.append(ev)
            r = ev.get("result") or {}
            print(f"  ✅ tool_result {ev['name']} total={r.get('total')} items={len(r.get('items') or [])}")
        elif t == "text_chunk":
            chunks.append(ev.get("content") or "")
        elif t == "history":
            history = ev
        elif t == "error":
            print(f"  ❌ ERROR {ev.get('message')}")
            raise AssertionError(ev.get("message"))

    final = "".join(chunks)
    print(f"  ✅ final answer ({len(final)} chars): {final[:120]}")

    # 基本链路检查
    assert len(tc_events) == 1
    assert tc_events[0]["name"] == cfg["tool_name"]
    assert len(tr_events) == 1
    assert history is not None

    # 关键：history 里必须有一条 user 消息注入了"本地聚合结果"，且类型匹配
    agg_injected_msg = None
    for m in history.get("content") or []:
        if m.get("role") == "user" and "本地聚合结果" in (m.get("content") or ""):
            agg_injected_msg = m
            break
    assert agg_injected_msg is not None, "_maybe_aggregate 应该注入聚合结果 user 消息"
    agg_content = agg_injected_msg["content"]
    m = re.search(r'"aggregation_type":\s*"([^"]+)"', agg_content)
    assert m, "聚合内容缺失 aggregation_type"
    agg_type = m.group(1)
    print(f"  ✅ 聚合类型 injection: {agg_type} (期望包含: {cfg['agg_type_keyword']})")
    assert cfg["agg_type_keyword"] in agg_type, f"期望 {cfg['agg_type_keyword']}, 实际 {agg_type}"

    # 交叉聚合 case：应该包含 breakdown
    if "and_status" in cfg["agg_type_keyword"]:
        assert "breakdown_by_status" in agg_content, "交叉聚合应该包含 breakdown_by_status"
        print(f"  ✅ 交叉聚合 breakdown: OK")
    return True


async def main():
    for ui in CASES:
        await run_case(ui)
    print("\n✅ ALL 4 time-aggregation E2E cases PASSED.")


if __name__ == "__main__":
    asyncio.run(main())
