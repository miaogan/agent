import json
import httpx
import sys

payload = {
    "user_input": "按状态分组统计所有问题单的数量，用中文表格列出来",
    "history": [],
}
url = "http://127.0.0.1:8000/api/chat/stream"

client = httpx.Client(timeout=300)
try:
    with client.stream("POST", url, json=payload, headers={"Accept": "text/event-stream"}) as r:
        print("HTTP", r.status_code, r.headers.get("content-type"))
        if r.status_code != 200:
            print(r.read().decode("utf-8", "ignore"))
            sys.exit(1)
        text_accum = ""
        history_cnt = 0
        tool_calls = 0
        tool_results = 0
        for line in r.iter_lines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                data = line[5:].strip()
            else:
                data = line
            if not data or data == "[DONE]":
                continue
            try:
                ev = json.loads(data)
            except Exception as e:
                print("BAD JSON:", data[:200], "ERR:", e)
                continue
            t = ev.get("type")
            if t == "text_chunk":
                text_accum += ev.get("content", "")
                if len(text_accum) % 40 < 3:
                    print(".", end="", flush=True)
            elif t == "tool_call":
                tool_calls += 1
                args_str = json.dumps(ev.get("arguments"), ensure_ascii=False)
                print("\n[TOOL_CALL]", ev.get("name"), args_str[:120])
            elif t == "tool_result":
                tool_results += 1
                r2 = ev.get("result")
                total = r2.get("total") if isinstance(r2, dict) else None
                ic = (len(r2.get("items", [])) if isinstance(r2, dict) and isinstance(r2.get("items"), list) else "-")
                print("\n[TOOL_RESULT]", ev.get("name"), "total=", total, "items_count=", ic)
            elif t == "history":
                history_cnt += 1
                roles = [m.get("role") for m in ev.get("content", []) if isinstance(m, dict)]
                print("\n[HISTORY] count=", len(ev.get("content", [])), "roles=", roles)
            elif t == "error":
                print("\n[ERROR]", ev.get("message"))
            else:
                print("\nUNKNOWN EVENT:", t, json.dumps(ev, ensure_ascii=False)[:120])
        print("\n\nFINAL ANSWER PREVIEW (first 600 chars):\n", text_accum[:600])
        print("\nstats: tool_calls=%d tool_results=%d history_events=%d" % (tool_calls, tool_results, history_cnt))
        print("answer chars:", len(text_accum))
finally:
    client.close()
