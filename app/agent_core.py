from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, AsyncIterator, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from app.aggregation import (
    aggregate_by_date,
    aggregate_by_field,
    aggregate_date_and_field,
    aggregate_two_level,
    stat_summary,
)
from app.history import extract_context_for_query, trim_history
from app.schemas import (
    ErrorEvent,
    HistoryEvent,
    MessageItem,
    Role,
    TextChunkEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.tools import get_issue_detail, search_issue_list


BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
MODEL_NAME = os.getenv("LLM_MODEL", "qwen3.5-9b")
MAX_HISTORY_ROUNDS = 3

# 测试覆盖用：设置后 node_agent 和 _astream_final_llm 会优先使用该 factory 而不是 build_llm。
_LLM_FACTORY_OVERRIDE: Any = None


def _get_llm(*, streaming: bool):
    global _LLM_FACTORY_OVERRIDE
    if _LLM_FACTORY_OVERRIDE is not None:
        return _LLM_FACTORY_OVERRIDE(streaming=streaming)
    return build_llm(streaming=streaming)


def build_llm(streaming: bool = True):
    return ChatOpenAI(
        base_url=BASE_URL,
        model=MODEL_NAME,
        api_key="lm-studio",
        temperature=0.2,
        streaming=streaming,
    )


TOOLS = [search_issue_list, get_issue_detail]
TOOL_MAP = {t.name: t for t in TOOLS}


SYSTEM_PROMPT = f"""你是问题单智能助手。

规则:
1. 无状态: 仅参考输入的近{MAX_HISTORY_ROUNDS}轮对话。
2. 数据必须调用工具获取，严禁编造。可用工具: search_issue_list(列表筛选19维)、get_issue_detail(单条详情)。
3. 远程接口不支持分组/统计;若需分组或按时间聚合统计(如按状态/项目/优先级等维度，或按年/季度/月/周/日/小时等时间粒度，或时间×维度交叉)，先查列表，随后框架会自动在本地计算聚合结果，你基于聚合结果用中文+markdown表格作答。
4. 若用户省略(如"刚才那些按项目分")，先从最近历史提取之前的筛选条件、已查过的issue_id、分组偏好，再据此调用工具。
5. 回答: 先中文结论/摘要，明细多时用markdown表格。只给用户自然语言，不要输出工具参数或内部过程。
"""


class AgentState(TypedDict, total=False):
    messages: list[BaseMessage]
    round_history: list[MessageItem]
    context: dict[str, Any]
    last_tool_result: Any
    aggregated_result: Any
    events: list[dict]
    final_assistant_content: str


def _lc_to_item(msg: BaseMessage) -> MessageItem:
    if isinstance(msg, HumanMessage):
        role = Role.USER
    elif isinstance(msg, AIMessage):
        role = Role.ASSISTANT
        tc = getattr(msg, "tool_calls", None) or None
        content = msg.content or ""
        if isinstance(content, list):
            content = "".join(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False) for c in content)
        return MessageItem(role=role, content=content, tool_calls=tc if tc else None)
    elif isinstance(msg, ToolMessage):
        role = Role.TOOL
        c = msg.content
        if not isinstance(c, str):
            c = json.dumps(c, ensure_ascii=False)
        return MessageItem(role=role, content=c, tool_call_id=msg.tool_call_id)
    else:
        role = Role.SYSTEM
    return MessageItem(role=role, content=msg.content or "")


def _items_to_lc(items: list[MessageItem]) -> list[BaseMessage]:
    out = []
    for it in items:
        if it.role == Role.USER:
            out.append(HumanMessage(content=it.content))
        elif it.role == Role.ASSISTANT:
            out.append(AIMessage(content=it.content or "", tool_calls=it.tool_calls or []))
        elif it.role == Role.TOOL:
            try:
                c = json.loads(it.content)
            except Exception:
                c = it.content
            out.append(ToolMessage(content=c, tool_call_id=it.tool_call_id or ""))
    return out


def _maybe_aggregate(last_tool_result: Any, context: dict) -> Optional[Any]:
    if not isinstance(last_tool_result, dict):
        return None
    items = last_tool_result.get("items")
    if not isinstance(items, list) or not items:
        return None
    date_agg = context.get("preferred_date_agg")
    pref = context.get("preferred_group_by")
    dim_field: Optional[str] = None
    if pref and isinstance(pref, str) and not pref.startswith("__date__"):
        dim_field = pref

    # ---- 时间 × 维度 交叉聚合（例如：按月+按状态）----
    if date_agg and dim_field:
        groups = aggregate_date_and_field(
            items,
            date_field=date_agg["date_field"],
            granularity=date_agg["granularity"],
            second_field=dim_field,
        )
        return {
            "aggregation_type": f"date_by_{date_agg['granularity']}_and_{dim_field}",
            "date_field": date_agg["date_field"],
            "granularity": date_agg["granularity"],
            "second_field": dim_field,
            "total_records_aggregated": len(items),
            "groups": groups,
        }

    # ---- 纯时间粒度聚合（例如：按创建年）----
    if date_agg:
        groups = aggregate_by_date(
            items,
            date_field=date_agg["date_field"],
            granularity=date_agg["granularity"],
            include_items=False,
        )
        return {
            "aggregation_type": f"group_by_date_{date_agg['granularity']}",
            "date_field": date_agg["date_field"],
            "granularity": date_agg["granularity"],
            "total_records_aggregated": len(items),
            "groups": groups,
        }

    # ---- 普通维度聚合 ----
    if isinstance(pref, str) and not pref.startswith("__date__"):
        groups = aggregate_by_field(items, pref, include_items=False)
        return {
            "aggregation_type": f"group_by_{pref}",
            "total_records_aggregated": len(items),
            "groups": groups,
        }
    summary = stat_summary(items)
    for k in ("by_status", "by_priority", "by_severity", "by_project", "by_assignee"):
        if isinstance(summary.get(k), list):
            for g in summary[k]:
                g.pop("items", None)
    return {"aggregation_type": "stat_summary", "total_records": len(items), "summary": summary}


def node_prepare(state: AgentState) -> AgentState:
    raw_history: list[MessageItem] = state.get("round_history", [])
    trimmed = trim_history(raw_history, MAX_HISTORY_ROUNDS)
    current_input = ""
    if trimmed and trimmed[-1].role == Role.USER:
        current_input = trimmed[-1].content
    context = extract_context_for_query(trimmed, current_input)
    sys_msg = SystemMessage(content=SYSTEM_PROMPT)
    lc_messages = [sys_msg] + _items_to_lc(trimmed)
    return {
        "messages": lc_messages,
        "round_history": trimmed,
        "context": context,
        "events": state.get("events", []),
    }


def node_agent(state: AgentState) -> AgentState:
    llm = _get_llm(streaming=False).bind_tools(TOOLS)
    messages: list[BaseMessage] = state["messages"]
    response: AIMessage = llm.invoke(messages)
    events: list[dict] = list(state.get("events", []))
    tcs = getattr(response, "tool_calls", None) or []
    for tc in tcs:
        events.append(ToolCallEvent(name=tc["name"], arguments=tc["args"] or {}).model_dump())
    return {
        "messages": messages + [response],
        "events": events,
    }


def _execute_tool_calls(ai_msg: AIMessage) -> tuple[list[ToolMessage], list[ToolResultEvent], Any]:
    tool_msgs: list[ToolMessage] = []
    result_events: list[ToolResultEvent] = []
    last_content: Any = None
    tool_calls = getattr(ai_msg, "tool_calls", None) or []
    for tc in tool_calls:
        name = tc["name"]
        args = tc.get("args") or {}
        tool = TOOL_MAP.get(name)
        if tool is None:
            content: Any = {"error": f"工具不存在: {name}"}
        else:
            try:
                content = tool.invoke(args)
            except Exception as e:
                content = {"error": f"工具执行异常: {type(e).__name__}: {e}"}
        last_content = content
        tcid = tc.get("id") or str(uuid.uuid4())
        if name == "search_issue_list" and isinstance(content, dict):
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
            prompt_payload = {
                "_note": "(仅为prompt摘要, SSE事件中已推全量结果)",
                "tool": "search_issue_list",
                "total": content.get("total"),
                "page": content.get("page"),
                "page_size": content.get("page_size"),
                "returned_items_count": len(items),
                "first_5_items_sample": sample,
            }
        elif name == "get_issue_detail" and isinstance(content, dict):
            prompt_payload = {
                "_note": "(仅为prompt摘要, SSE事件中已推全量结果)",
                "tool": "get_issue_detail",
                "issue_id": content.get("issue_id"),
                "title": str(content.get("title") or "")[:60],
                "status": content.get("status"),
                "priority": content.get("priority"),
                "creator": content.get("creator"),
                "assignee": content.get("assignee"),
                "project": content.get("project"),
                "fault_cause": str(content.get("fault_cause") or "")[:60],
                "created_at": content.get("created_at"),
            }
        else:
            prompt_payload = content if isinstance(content, dict) and len(json.dumps(content, ensure_ascii=False)) < 500 else {"tool_result": True}
        tool_msgs.append(ToolMessage(content=prompt_payload, tool_call_id=tcid, name=name))
        result_events.append(ToolResultEvent(name=name, result=content))
    return tool_msgs, result_events, last_content


def node_tools(state: AgentState) -> AgentState:
    messages: list[BaseMessage] = state["messages"]
    last = messages[-1]
    events: list[dict] = list(state.get("events", []))
    if not isinstance(last, AIMessage):
        return {"events": events}
    tool_msgs, result_events, last_content = _execute_tool_calls(last)
    for ev in result_events:
        events.append(ev.model_dump())
    ctx = state.get("context", {})
    aggregated = _maybe_aggregate(last_content, ctx)

    new_msgs = list(messages)
    new_msgs.extend(tool_msgs)

    if aggregated is not None:
        extra_msg = HumanMessage(
            content="[本地聚合结果(远程接口不支持分组/统计，请基于此结果给用户结论，用markdown表格)]\n"
            + json.dumps(aggregated, ensure_ascii=False, indent=2)
        )
        new_msgs.append(extra_msg)

    return {
        "messages": new_msgs,
        "last_tool_result": last_content,
        "aggregated_result": aggregated,
        "events": events,
    }


def _has_tool_call(msg: BaseMessage) -> bool:
    if not isinstance(msg, AIMessage):
        return False
    return bool(getattr(msg, "tool_calls", None))


def router_after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    if _has_tool_call(last):
        return "tools"
    return END


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("prepare", node_prepare)
    g.add_node("agent", node_agent)
    g.add_node("tools", node_tools)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "agent")
    g.add_conditional_edges("agent", router_after_agent, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()


GRAPH = build_graph()


async def _run_graph_once(round_history: list[MessageItem]) -> AgentState:
    final_state: Optional[AgentState] = None
    loop = asyncio.get_event_loop()
    it = iter(GRAPH.stream({"round_history": round_history}, stream_mode="values"))

    def _next_state():
        nonlocal final_state
        try:
            s = next(it)
            final_state = s
            return True
        except StopIteration:
            return False

    while True:
        has_more = await loop.run_in_executor(None, _next_state)
        if not has_more:
            break
    if final_state is None:
        raise RuntimeError("Graph未产生任何state")
    return final_state


async def _astream_final_llm(messages: list[BaseMessage]) -> AsyncIterator[str]:
    llm = _get_llm(streaming=True)
    loop = asyncio.get_event_loop()
    it = iter(llm.stream(messages))

    def _next_chunk() -> Optional[str]:
        try:
            chunk = next(it)
            return getattr(chunk, "content", "") or ""
        except StopIteration:
            return None

    while True:
        text = await loop.run_in_executor(None, _next_chunk)
        if text is None:
            return
        if text:
            yield text


async def run_agent_stream(
    user_input: str,
    history: list[MessageItem | dict],
    llm_factory: Any = None,
) -> AsyncIterator[dict]:
    """无状态流式Agent执行:
    - 1. 透传 tool_call / tool_result 事件
    - 2. SSE流式 text_chunk 推送最终回答
    - 3. 最后以 {type:history, content:[近三轮消息]} 收尾，供客户端下一轮传入。
    history 支持两种形式: MessageItem 对象列表 或 客户端从 history 事件解析回传的 dict 列表
    llm_factory: 可选，传入一个 callable(streaming: bool) -> BaseChatModel，用于在测试中替换真实 LLM。
    """
    global _LLM_FACTORY_OVERRIDE
    prev_factory = _LLM_FACTORY_OVERRIDE
    _LLM_FACTORY_OVERRIDE = llm_factory
    try:
        norm_history: list[MessageItem] = []
        for m in history or []:
            if isinstance(m, MessageItem):
                norm_history.append(m)
            elif isinstance(m, dict):
                try:
                    norm_history.append(MessageItem.model_validate(m))
                except Exception:
                    continue
        new_user_msg = MessageItem(role=Role.USER, content=user_input)
        round_history_input = norm_history + [new_user_msg]
        trimmed_input = trim_history(round_history_input, MAX_HISTORY_ROUNDS)

        try:
            final_state = await _run_graph_once(trimmed_input)
        except Exception as e:
            yield ErrorEvent(message=f"Graph执行失败: {type(e).__name__}: {e}").model_dump(mode="json")
            return

        for ev in final_state.get("events", []):
            # ev 已被 ToolCallEvent/ToolResultEvent.model_dump() 序列化，确保无 enum
            if isinstance(ev, dict) and "name" in ev and ("arguments" in ev or "result" in ev):
                yield ev
            else:
                yield ev

        final_messages: list[BaseMessage] = final_state["messages"]

        last_ai_msg: Optional[AIMessage] = None
        for m in reversed(final_messages):
            if isinstance(m, AIMessage) and not _has_tool_call(m) and (m.content or "").strip():
                last_ai_msg = m
                break

        full_text = ""
        if last_ai_msg is not None and last_ai_msg.content:
            for ch in last_ai_msg.content:
                full_text += ch
                yield TextChunkEvent(content=ch).model_dump(mode="json")
                await asyncio.sleep(0.003)
        else:
            try:
                async for chunk_text in _astream_final_llm(final_messages):
                    full_text += chunk_text
                    yield TextChunkEvent(content=chunk_text).model_dump(mode="json")
            except Exception as e:
                yield ErrorEvent(message=f"流式生成失败: {type(e).__name__}: {e}").model_dump(mode="json")
                return

        lc_msgs_no_sys = [m for m in final_messages if not isinstance(m, SystemMessage)]
        new_items = [_lc_to_item(m) for m in lc_msgs_no_sys]
        if full_text and (not new_items or new_items[-1].role != Role.ASSISTANT or not new_items[-1].content):
            new_items.append(MessageItem(role=Role.ASSISTANT, content=full_text))

        existing_keys = {(m.role, (m.content or "")[:120]) for m in trimmed_input}
        merged = list(trimmed_input)
        for it in new_items:
            key = (it.role, (it.content or "")[:120])
            if key in existing_keys:
                continue
            merged.append(it)
            existing_keys.add(key)

        trimmed_final = trim_history(merged, MAX_HISTORY_ROUNDS)
        yield HistoryEvent(content=trimmed_final).model_dump(mode="json")
    finally:
        _LLM_FACTORY_OVERRIDE = prev_factory
