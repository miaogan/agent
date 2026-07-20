import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from app.tools import search_issue_list, get_issue_detail
from app.agent_core import SYSTEM_PROMPT

captured = {}


def hook(request):
    body = request.content
    if body:
        try:
            captured["body"] = json.loads(body.decode("utf-8"))
        except Exception:
            captured["raw"] = body[:200000]


client = httpx.Client(event_hooks={"request": [hook]}, timeout=httpx.Timeout(1.0))

llm = ChatOpenAI(
    base_url="http://127.0.0.1:1234/v1",
    model="qwen3.5-9b",
    api_key="lm-studio",
    temperature=0.1,
    http_client=client,
    max_retries=0,
)
bound = llm.bind_tools([search_issue_list, get_issue_detail])
try:
    bound.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content="按状态分组统计所有问题单的数量"),
    ])
except Exception as e:
    print("请求被拦截或超时:", type(e).__name__, str(e)[:120])

body = captured.get("body")
if not body:
    print("没有捕获到body，raw =", captured.get("raw"))
    sys.exit(1)

raw = json.dumps(body, ensure_ascii=False)
import re
chinese = len(re.findall(r"[\u4e00-\u9fff]", raw))
other = len(raw) - chinese
tokens = int(chinese / 1.5 + other / 4 + 0.5)
print(f"\n实际body chars: {len(raw)}   估算tokens: {tokens}   模型上限=16384")

tools_payload = body.get("tools") or []
for i, t in enumerate(tools_payload):
    t_str = json.dumps(t, ensure_ascii=False)
    fn = t.get("function", {})
    params = fn.get("parameters", {})
    print(f"\ntool[{i}] {fn.get('name')}: total chars={len(t_str)}  ~{int(len(t_str)/4)}tokens")
    print(f"  properties: {list(params.get('properties', {}).keys())}")
    if "$defs" in params:
        print(f"  $defs keys({len(params['$defs'])}): {list(params['$defs'].keys())[:10]}")
        for dk, dv in list(params["$defs"].items())[:2]:
            dstr = json.dumps(dv, ensure_ascii=False)
            print(f"    $defs.{dk} chars={len(dstr)}")
            print(f"      snippet: {dstr[:300]}")

print("\n---- body 前 1800 chars ----")
print(raw[:1800])
