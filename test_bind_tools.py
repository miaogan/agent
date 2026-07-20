import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.tools import search_issue_list, get_issue_detail
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI
import os

print("=== 工具 bind_tools 测试 ===")

llm = ChatOpenAI(
    base_url=os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1"),
    model="qwen3.5-9b",
    api_key="lm-studio",
    temperature=0.1,
)
bound = llm.bind_tools([search_issue_list, get_issue_detail])
msg = HumanMessage(content="请查询所有OPEN状态的问题单，然后按状态分组告诉我各有多少条")

print("调用模型（期望返回带tool_calls的AIMessage）...")
try:
    out: AIMessage = bound.invoke([
        SystemMessage(content="你是问题单助手，有查询需要就调用search_issue_list工具，不要编造数据。"),
        msg,
    ])
    print("AIMessage 类型:", type(out).__name__)
    print("content 前80字:", (out.content or "")[:80])
    tcs = getattr(out, "tool_calls", None)
    print("tool_calls:", tcs)
    if tcs:
        print("执行工具调用...")
        tc = tcs[0]
        name = tc["name"]
        args = tc["args"]
        print(f"  tool: {name}")
        print(f"  args: {args}")
        from app.tools import TOOL_MAP as _TM
        if name in _TM:
            res = _TM[name].invoke(args or {})
            total = res.get("total") if isinstance(res, dict) else None
            count_items = len(res.get("items", [])) if isinstance(res, dict) else None
            print(f"  OK: total={total}, items={count_items}")
        else:
            print(f"  工具名未知: {name}")
    else:
        print("模型没有调用工具（可能不需要，或指令没命中工具调用）")
except Exception as e:
    print(f"[!] 失败: {type(e).__name__}: {e}")
