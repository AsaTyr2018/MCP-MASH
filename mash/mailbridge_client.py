from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Coroutine

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Implementation

from . import __version__
from .runtime_config import RuntimeConfig


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def target() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            error["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join()
    if "error" in error:
        raise error["error"]
    return result.get("value")


class MailbridgeClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def configured(self) -> bool:
        return bool(self.config.mailbridge_mcp_url and self.config.mailbridge_mcp_token)

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self.configured():
            raise ValueError("Mailbridge MCP adapter is not configured")
        return run_async(self._call(tool_name, arguments))

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {self.config.mailbridge_mcp_token}"}
        async with streamablehttp_client(self.config.mailbridge_mcp_url, headers=headers) as (read, write, _):
            async with ClientSession(
                read,
                write,
                client_info=Implementation(
                    name="mcp-mash",
                    title="MCP-MASH Mail Automation Script Host",
                    version=__version__,
                ),
            ) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                if not result.content:
                    return None
                texts = [getattr(content, "text", str(content)) for content in result.content]
                if len(texts) == 1:
                    return texts[0]
                return texts

    def list_accounts(self) -> list[dict[str, Any]]:
        raw = self.call("list_accounts", {})
        if isinstance(raw, list):
            return [json.loads(item) if isinstance(item, str) else item for item in raw]
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [parsed]

    def account_id_by_name(self, name: str) -> int:
        accounts = self.list_accounts()
        for account in accounts:
            if str(account.get("name", "")) == name:
                return int(account["id"])
        raise ValueError(f"Mailbridge account '{name}' is not visible to this MASH token")

    def sync_account(self, account_id: int, limit: int = 100) -> Any:
        return self.call("sync_account", {"account_id": account_id, "limit": limit})

    def search_mail(self, account_id: int, query: str, limit: int = 20) -> list[dict[str, Any]]:
        raw = self.call("search_mail", {"account_id": account_id, "query": query, "limit": limit})
        if isinstance(raw, list):
            return [json.loads(item) if isinstance(item, str) else item for item in raw]
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [parsed]

    def move_messages(self, account_id: int, message_ids: list[int], target_folder: str, source_folder: str = "") -> Any:
        return self.call(
            "move_messages",
            {
                "account_id": account_id,
                "message_ids": message_ids,
                "target_folder": target_folder,
                "source_folder": source_folder,
            },
        )
