import asyncio
import json

import dotenv

dotenv.load_dotenv()

from app.agent_core import run_agent_stream


async def chat(history: list[dict], query: str, turn_no: int) -> list[dict]:
    print("=" * 60)
    print(f"第 {turn_no} 轮 用户输入: {query}")
    print("=" * 60)
    if turn_no == 1:
        assert len(history) == 0, "初始history为空"
    text_accum = ""
    new_history: list[dict] = []
    async for event in run_agent_stream(query, history):
        t = event["type"]
        if t == "tool_call":
            args_str = json.dumps(event["arguments"], ensure_ascii=False)
            print(f"[TOOL_CALL] {event['name']}({args_str[:140]})")
        elif t == "tool_result":
            r = event["result"]
            total = r.get("total") if isinstance(r, dict) else None
            if isinstance(r, dict) and "items" in r:
                n = len(r["items"])
                print(f"[TOOL_RESULT] {event['name']} -> total={total}, items_count={n}")
            elif isinstance(r, dict) and "issue_id" in r:
                print(f"[TOOL_RESULT] {event['name']} -> issue_id={r['issue_id']}, title={(r.get('title') or '')[:40]}")
            else:
                print(f"[TOOL_RESULT] {event['name']} -> keys={list(r.keys()) if isinstance(r, dict) else type(r)}")
        elif t == "text_chunk":
            text_accum += event["content"]
            if len(text_accum) % 60 < 5:
                print(".", end="", flush=True)
        elif t == "history":
            new_history = event["content"]
            print()
            print(f"[HISTORY] 条数={len(new_history)}")
            roles = [m.get("role") for m in new_history]
            print(f"  roles={roles}")
            for i, m in enumerate(new_history):
                cnt_preview = (m.get("content") or "")
                if isinstance(cnt_preview, str):
                    cnt_preview = cnt_preview.replace("\n", " ")[:80]
                else:
                    cnt_preview = str(cnt_preview)[:80]
                print(f"   #{i} {m.get('role')} {m.get('name','')} : {cnt_preview}")
        elif t == "error":
            print("\n[ERROR]", event["message"])
        else:
            print("? 未知事件", t, event)
    print(f"\n--- 第{turn_no}轮 最终回答预览 (前240 chars) ---")
    print(text_accum[:240])
    print()
    return new_history


async def main():
    history: list[dict] = []
    # 第一轮：列表聚合
    history = await chat(history, "帮我查一下李娜创建的问题单，状态为开放中的，并按优先级分组数量", 1)
    print("检查 history 所有元素 role 均为字符串:", all(isinstance(m.get("role"), str) for m in history))
    # 第二轮：基于上一轮上下文追问，不直接给参数，agent 必须从 history 中提取"李娜创建 + 开放中 + 优先级分组结果"再做二次聚合
    history = await chat(history, "在刚才结果的基础上，再按项目分一下各组的数量", 2)
    # 第三轮：再追问
    history = await chat(history, "再按严重程度分组", 3)
    # 第四轮：近三轮原则，此时第一轮应已被剔除。我们问"第一轮的用户输入中'创建人'是谁" —— 如果没有第一轮，agent 只能看到2/3轮，应回答不知道或找不见
    history = await chat(history, "第一轮用户问题中指定的创建人是谁？", 4)


if __name__ == "__main__":
    asyncio.run(main())
