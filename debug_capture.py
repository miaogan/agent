import sys, json, asyncio, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import httpx
from langchain_openai import ChatOpenAI
import app.agent_core as ac

captured_bodies = []


def make_client_wrap():
    def hook(request):
        body = request.content
        if body:
            try:
                captured_bodies.append(json.loads(body.decode("utf-8")))
            except Exception:
                pass
    return httpx.Client(event_hooks={"request": [hook]}, timeout=httpx.Timeout(180.0), max_redirects=3)


def patched_build_llm(streaming=True):
    return ChatOpenAI(
        base_url="http://127.0.0.1:1234/v1",
        model="qwen3.5-9b",
        api_key="lm-studio",
        temperature=0.2,
        streaming=streaming,
        http_client=make_client_wrap(),
    )


ac.build_llm = patched_build_llm

from app.schemas import MessageItem, Role


async def main():
    try:
        async for ev in ac.run_agent_stream("按状态分组统计所有问题单的数量，用中文表格列出来", []):
            if ev.get("type") == "error":
                print("ERROR:", ev["message"])
                break
    except Exception as e:
        print("Outer exception:", type(e).__name__, e)

    print(f"\n捕获到 {len(captured_bodies)} 个请求体")
    for i, body in enumerate(captured_bodies):
        raw = json.dumps(body, ensure_ascii=False)
        chinese = len(re.findall(r"[\u4e00-\u9fff]", raw))
        other = len(raw) - chinese
        tokens = int(chinese / 1.5 + other / 4 + 0.5)
        print(f"\n--- 请求#{i+1}: chars={len(raw)}  ~{tokens}tokens ---")
        msgs = body.get("messages") or []
        def _safe(m):
            if not isinstance(m, dict):
                return 0, "(non-dict)"
            c = m.get("content")
            if c is None:
                return 0, "None"
            if isinstance(c, list):
                try:
                    return len(json.dumps(c, ensure_ascii=False)), "list-content"
                except Exception:
                    return len(str(c)), "list-as-str"
            return len(str(c)), m.get("role", "?")
        parts = [_safe(m) for m in msgs]
        print(f"  messages数: {len(msgs)}  role&chars: {parts}")
        tools = body.get("tools") or []
        for j, t in enumerate(tools):
            s = json.dumps(t, ensure_ascii=False)
            print(f"  tool[{j}] chars={len(s)}")
        for j, m in enumerate(msgs):
            chars, role = parts[j]
            if chars > 3000:
                if isinstance(m, dict):
                    c = m.get("content") or ""
                else:
                    c = str(m)
                if not isinstance(c, str):
                    c = json.dumps(c, ensure_ascii=False)
                print(f"\n  [msg#{j} role={role} chars={chars}] 前1000:")
                print(c[:1000])
                print(f"  [msg#{j}] 后1000:")
                print(c[-1000:])


asyncio.run(main())
