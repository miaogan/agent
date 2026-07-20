import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from app.tools import search_issue_list, get_issue_detail
from app.agent_core import SYSTEM_PROMPT


def rough_tokens(s: str) -> int:
    import re
    chinese = len(re.findall(r"[\u4e00-\u9fff]", s))
    other = len(s) - chinese
    return int(chinese / 1.5 + other / 4 + 0.5)


payload_tools = []
for t in [search_issue_list, get_issue_detail]:
    try:
        schema = t.args_schema.model_json_schema()
    except Exception:
        schema = {}
    payload_tools.append({
        "type": "function",
        "function": {"name": t.name, "description": t.description, "parameters": schema},
    })

msgs = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": "按状态分组统计所有问题单的数量，用中文表格列出来"},
]
raw = json.dumps({"model": "x", "messages": msgs, "tools": payload_tools}, ensure_ascii=False)
print(f"raw payload chars: {len(raw)}")
print(f"rough tokens:      {rough_tokens(raw)}  (模型context上限=16384)")
print()
print(f"SYSTEM_PROMPT: chars={len(SYSTEM_PROMPT)}  ~{rough_tokens(SYSTEM_PROMPT)} tokens")
for t in [search_issue_list, get_issue_detail]:
    try:
        schema = json.dumps(t.args_schema.model_json_schema(), ensure_ascii=False)
    except Exception:
        schema = "{}"
    print(f"tool {t.name}: desc={len(t.description)}chars  schema={len(schema)}chars  ~{rough_tokens(t.description+schema)}tokens")
