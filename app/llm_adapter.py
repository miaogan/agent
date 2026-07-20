from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Iterator, Optional

import httpx
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.tools import BaseTool

BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:1234")
MODEL_NAME = os.getenv("LLM_MODEL", "qwen3.5-9b")


def _format_messages_openai(messages: list[BaseMessage]) -> list[dict]:
    out = []
    for m in messages:
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": m.content})
        elif isinstance(m, HumanMessage):
            out.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            d = {"role": "assistant", "content": m.content}
            tcs = getattr(m, "tool_calls", None)
            if tcs:
                d["tool_calls"] = tcs
            out.append(d)
        elif isinstance(m, ToolMessage):
            out.append({"role": "tool", "content": json.dumps(m.content, ensure_ascii=False), "tool_call_id": m.tool_call_id})
        else:
            out.append({"role": "user", "content": m.content})
    return out


def _format_simple(messages: list[BaseMessage]) -> tuple[str, str]:
    system = "You are a helpful assistant."
    user_parts = []
    for m in messages:
        if isinstance(m, SystemMessage):
            system = m.content
        elif isinstance(m, HumanMessage):
            user_parts.append(m.content)
        elif isinstance(m, AIMessage) and m.content:
            user_parts.append(f"(assistant){m.content}")
        elif isinstance(m, ToolMessage):
            c = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
            user_parts.append(f"(tool_result){c}")
    return system, "\n\n".join(user_parts) or " "


async def _probe_endpoint() -> str:
    """探测本地服务更支持哪种接口，返回 'openai' 或 'simple'。"""
    for path, mode in [("/v1/chat/completions", "openai"), ("/v1/models", "openai"), ("/api/v1/chat", "simple")]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(BASE_URL.rstrip("/") + path)
                if r.status_code < 500:
                    return mode
        except Exception:
            continue
    return "simple"


_ENDPOINT_MODE: Optional[str] = None


async def get_endpoint_mode() -> str:
    global _ENDPOINT_MODE
    if _ENDPOINT_MODE is None:
        _ENDPOINT_MODE = await _probe_endpoint()
    return _ENDPOINT_MODE


class IssueAgentChatModel(SimpleChatModel):
    """自定义 ChatModel：自动适配本地 LLM 的两种接口。"""

    model: str = MODEL_NAME
    temperature: float = 0.2
    streaming: bool = True
    bind_tools_list: list = []

    def _call(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        mode = asyncio.run(self._get_mode())
        return asyncio.run(self._acall_inner(mode, messages))

    async def _agenerate(
        self,
        messages: list[list[BaseMessage]],
        stop: Optional[list[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> LLMResult:
        results = []
        for msgs in messages:
            text = await self._call_async(msgs)
            msg = AIMessage(content=text)
            results.append(ChatGeneration(message=msg))
        return LLMResult(generations=[results])

    async def _call_async(self, messages: list[BaseMessage]) -> str:
        mode = await self._get_mode()
        return await self._acall_inner(mode, messages)

    async def _get_mode(self) -> str:
        return await get_endpoint_mode()

    async def _acall_inner(self, mode: str, messages: list[BaseMessage]) -> str:
        url = BASE_URL.rstrip("/")
        if mode == "openai":
            url += "/v1/chat/completions"
            payload = {
                "model": self.model,
                "messages": _format_messages_openai(messages),
                "temperature": self.temperature,
                "stream": False,
            }
            if self.bind_tools_list:
                payload["tools"] = [self._tool_to_schema(t) for t in self.bind_tools_list]
            async with httpx.AsyncClient(timeout=180.0) as c:
                r = await c.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
            try:
                return data["choices"][0]["message"].get("content", "") or ""
            except Exception:
                return str(data)
        else:
            url += "/api/v1/chat"
            system_prompt, user_input = _format_simple(messages)
            payload = {
                "model": self.model,
                "system_prompt": system_prompt,
                "input": user_input,
            }
            async with httpx.AsyncClient(timeout=180.0) as c:
                r = await c.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
            if isinstance(data, dict):
                for k in ("message", "response", "output", "content", "result", "text"):
                    if k in data and isinstance(data[k], str):
                        return data[k]
            return str(data)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator:
        text = self._call(messages)
        for i in range(0, len(text), 4):
            chunk = type("Chunk", (), {"content": text[i:i+4]})()
            yield chunk

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator:
        text = await self._call_async(messages)
        for i in range(0, len(text), 4):
            chunk = type("Chunk", (), {"content": text[i:i+4]})()
            yield chunk

    @staticmethod
    def _tool_to_schema(tool: BaseTool) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": getattr(tool, "args_schema", None) and tool.args_schema.model_json_schema() or {
                    "type": "object",
                    "properties": {},
                },
            },
        }

    @property
    def _llm_type(self) -> str:
        return "issue-agent-chat"

    def bind_tools(self, tools: list, **kwargs: Any):
        self.bind_tools_list = list(tools)
        return self


# LLMResult import moved to top

