from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app.agent_core import run_agent_stream
from app.schemas import ChatRequest


app = FastAPI(title="Issue Ticket Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Issue Ticket Agent Demo</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 920px; margin: 0 auto; padding: 24px; background: #f5f6fa; }
  h1 { font-size: 20px; margin: 0 0 16px; }
  #history-box { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; height: 420px; overflow-y: auto; margin-bottom: 12px; font-size: 14px; }
  .msg { margin-bottom: 10px; padding: 8px 10px; border-radius: 6px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
  .msg.user { background: #eaf2ff; border-left: 3px solid #3b82f6; }
  .msg.assistant { background: #f0fdf4; border-left: 3px solid #22c55e; }
  .msg.tool { background: #fffbeb; border-left: 3px solid #f59e0b; font-family: Consolas, monospace; font-size: 12px; }
  #input-row { display: flex; gap: 8px; }
  #input { flex: 1; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; }
  button { padding: 10px 16px; border: 0; border-radius: 6px; background: #3b82f6; color: #fff; font-size: 14px; cursor: pointer; }
  button:disabled { background: #9ca3af; cursor: not-allowed; }
  #status { margin-top: 8px; font-size: 12px; color: #6b7280; }
  .history-summary { margin-top: 16px; padding: 10px; background: #eef2ff; border-radius: 6px; font-size: 12px; }
  .example { display: inline-block; padding: 4px 8px; background: #fff; border: 1px solid #e5e7eb; border-radius: 4px; margin: 4px 4px 0 0; cursor: pointer; font-size: 12px; color: #374151; }
</style>
</head>
<body>
<h1>🐛 问题单智能助手</h1>
<div style="margin-bottom:10px">
  <span class="example" onclick="fill('查询所有OPEN状态的问题单')">① 查OPEN状态</span>
  <span class="example" onclick="fill('按状态分组统计所有问题单的数量')">② 按状态分组(聚合)</span>
  <span class="example" onclick="fill('张伟创建的高优先级问题单，按项目分组')">③ 复合筛选+聚合</span>
  <span class="example" onclick="fill('查询ISS-12345的详情')">④ 查单条详情</span>
  <span class="example" onclick="fill('刚才那些按严重程度再分一下')">⑤ 基于历史提问</span>
</div>
<div id="history-box"></div>
<div id="input-row">
  <input id="input" placeholder="输入问题(支持自然语言筛选/分组)，回车发送" autocomplete="off"/>
  <button id="btn-send" onclick="send()">发送</button>
</div>
<div id="status">就绪 · history 由客户端维护，近3轮自动截断</div>
<div class="history-summary" id="history-summary">当前 history 共 0 条</div>

<script>
var chatHistory = [];
var box = document.getElementById('history-box');
var input = document.getElementById('input');
var sendBtn = document.getElementById('btn-send');
var statusEl = document.getElementById('status');
var summaryEl = document.getElementById('history-summary');

window.fill = function(t) { input.value = t; input.focus(); };
input.addEventListener('keydown', function(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); window.send(); } });

function appendMsg(role, text, extraClass) {
  if (extraClass === undefined) extraClass = '';
  var d = document.createElement('div');
  d.className = 'msg ' + role + ' ' + extraClass;
  var labels = { user: 'User', assistant: 'Agent', tool: 'Tool' };
  var label = labels[role] ? labels[role] : role;
  d.innerHTML = '<div style="font-weight:600;margin-bottom:4px">' + label + '</div><div></div>';
  d.querySelector('div:last-child').textContent = text;
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
  return d.querySelector('div:last-child');
}

function updateSummary() {
  var counts = {};
  for (var i = 0; i < chatHistory.length; i++) {
    var m = chatHistory[i];
    counts[m.role] = (counts[m.role] || 0) + 1;
  }
  summaryEl.textContent = 'history: ' + chatHistory.length + ' items, roles: ' + JSON.stringify(counts);
}

window.send = function() {
  var text = input.value.trim();
  if (!text) return;
  input.value = '';
  sendBtn.disabled = true;
  statusEl.textContent = 'requesting...';
  appendMsg('user', text);

  var userCount = 0;
  for (var j = 0; j < chatHistory.length; j++) if (chatHistory[j].role === 'user') userCount++;
  var startIdx = 0;
  if (userCount >= 3) {
    var seen = 0;
    for (var k = 0; k < chatHistory.length; k++) {
      if (chatHistory[k].role === 'user') {
        if (seen >= userCount - 3 + 1) break;
        seen++;
        startIdx = k;
      }
    }
  }
  var payload = { user_input: text, history: chatHistory.slice(startIdx) };

  var assistantNode = null;
  var assistantText = '';

  function handleEvent(ev) {
    if (ev.type === 'text_chunk') {
      if (!assistantNode) assistantNode = appendMsg('assistant', '');
      assistantText += ev.content;
      assistantNode.textContent = assistantText;
    } else if (ev.type === 'tool_call') {
      appendMsg('tool', '[call] ' + ev.name + '\\nargs: ' + JSON.stringify(ev.arguments, null, 2));
    } else if (ev.type === 'tool_result') {
      var r = typeof ev.result === 'string' ? ev.result : JSON.stringify(ev.result, null, 2);
      var snippet = r.length > 2000 ? r.slice(0, 2000) + '\\n...(truncated)' : r;
      appendMsg('tool', '[result] ' + ev.name + '\\n' + snippet);
    } else if (ev.type === 'history') {
      chatHistory.length = 0;
      for (var mi = 0; mi < ev.content.length; mi++) chatHistory.push(ev.content[mi]);
      updateSummary();
    } else if (ev.type === 'error') {
      appendMsg('assistant', 'error: ' + ev.message);
    }
  }

  function finish() {
    sendBtn.disabled = false;
  }

  try {
    fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function(resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      var reader = resp.body.getReader();
      var decoder = new TextDecoder('utf-8');
      var buf = '';
      function pump() {
        return reader.read().then(function(chunk) {
          var value = chunk.value, done = chunk.done;
          if (done) {
            if (buf) {
              var t = buf.trim();
              if (t && t.charAt(0) !== ':' && t.indexOf('data:') === 0) {
                var dataStr = t.slice(5).trim();
                if (dataStr && dataStr !== '[DONE]') {
                  try { handleEvent(JSON.parse(dataStr)); } catch(_) {}
                }
              }
            }
            statusEl.textContent = 'done';
            finish();
            return;
          }
          buf += decoder.decode(value || new Uint8Array(), { stream: true });
          var lines = buf.split('\\n');
          buf = lines.pop() || '';
          for (var li = 0; li < lines.length; li++) {
            var t = lines[li].trim();
            if (!t) continue;
            if (t.charAt(0) === ':') continue;
            if (t.indexOf('event:') === 0) continue;
            if (t.indexOf('data:') !== 0) continue;
            var ds = t.slice(5).trim();
            if (!ds || ds === '[DONE]') continue;
            try { handleEvent(JSON.parse(ds)); } catch(_) {}
          }
          return pump();
        });
      }
      return pump();
    }).catch(function(e) {
      statusEl.textContent = 'error: ' + e.message;
      if (!assistantNode) appendMsg('assistant', 'request failed: ' + e.message);
      finish();
    });
  } catch (e) {
    statusEl.textContent = 'exception: ' + e.message;
    finish();
  }
};

updateSummary();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    from sse_starlette.sse import ServerSentEvent

    async def gen():
        try:
            async for ev in run_agent_stream(req.user_input, req.history):
                yield ServerSentEvent(data=json.dumps(ev, ensure_ascii=False), event=ev.get("type", "message"))
        except Exception as e:
            err = {"type": "error", "message": f"{type(e).__name__}: {e}"}
            yield ServerSentEvent(data=json.dumps(err, ensure_ascii=False), event="error")

    return EventSourceResponse(gen())
