import sys
mods = ["fastapi","uvicorn","sse_starlette","langchain","langchain_core","langchain_openai","langgraph","pydantic","httpx"]
missing = []
for m in mods:
    try:
        __import__(m)
    except Exception as e:
        missing.append((m, type(e).__name__, str(e)[:60]))
print("Python:", sys.executable)
for row in missing:
    print("MISSING:", row)
if not missing:
    print("ALL DEPS OK")
