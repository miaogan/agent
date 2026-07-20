import asyncio
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.agent_core import run_agent_stream
from app.schemas import MessageItem, Role


async def main():
    user_input = "按状态分组统计所有问题单的数量，用中文表格列出来"
    history: list[MessageItem] = []

    print("=== 用户输入 ===")
    print(user_input)
    print("\n=== 流式事件 ===")
    history_out = None
    last_text = ""
    async for ev in run_agent_stream(user_input, history):
        t = ev.get("type")
        if t == "tool_call":
            print(f"\n[tool_call] {ev['name']} args={json.dumps(ev['arguments'], ensure_ascii=False)[:300]}")
        elif t == "tool_result":
            r = ev.get("result")
            summary = ""
            if isinstance(r, dict):
                if "total" in r:
                    summary = f"total={r.get('total')}, items={len(r.get('items', []))}"
                elif "issue_id" in r:
                    summary = f"issue_id={r.get('issue_id')}, 30 fields"
                else:
                    summary = f"keys={list(r.keys())[:8]}"
            else:
                summary = str(r)[:150]
            print(f"[tool_result] {ev['name']} -> {summary}")
        elif t == "text_chunk":
            last_text += ev["content"]
            print(".", end="", flush=True)
        elif t == "history":
            history_out = ev["content"]
            print("\n\n[history] 消息数量:", len(history_out))
            roles = []
            for m in history_out:
                if isinstance(m, dict):
                    roles.append(m.get("role"))
                else:
                    roles.append(m.role.value if hasattr(m.role,"value") else m.role)
            user_rounds = sum(1 for r in roles if r == "user")
            asst_rounds = sum(1 for r in roles if r == "assistant")
            tool_rounds = sum(1 for r in roles if r == "tool")
            print(f"  user={user_rounds}  assistant={asst_rounds}  tool={tool_rounds}")
            print("  roles顺序:", roles)
        elif t == "error":
            print("\n[ERROR]", ev["message"])
        else:
            print("? 未知事件", t, ev)

    print("\n=== 最终回答 ===")
    print(last_text[:1200])
    if len(last_text) > 1200:
        print("...(截断)")


asyncio.run(main())
