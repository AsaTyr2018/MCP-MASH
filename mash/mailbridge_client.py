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
        return _coerce_records(raw)

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
        return _coerce_records(raw)

    def get_message(self, message_id: int) -> dict[str, Any]:
        return _coerce_object(self.call("get_message", {"message_id": message_id}))

    def list_attachments(self, message_id: int) -> dict[str, Any]:
        return _coerce_object(self.call("list_attachments", {"message_id": message_id}))

    def get_attachment(self, message_id: int, attachment_index: int = 0, filename: str = "", max_bytes: int = 1_000_000) -> dict[str, Any]:
        return _coerce_object(
            self.call(
                "get_attachment",
                {
                    "message_id": message_id,
                    "attachment_index": attachment_index,
                    "filename": filename,
                    "max_bytes": max_bytes,
                },
            )
        )

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

    def search_contacts(self, account_id: int, query: str = "", limit: int = 20) -> dict[str, Any]:
        return _coerce_object(self.call("search_contacts", {"account_id": account_id, "query": query, "limit": limit}))

    def create_contact(
        self,
        account_id: int,
        display_name: str,
        email: str,
        phone: str = "",
        company: str = "",
        profile_id: int | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "account_id": account_id,
            "display_name": display_name,
            "email": email,
            "phone": phone,
            "company": company,
        }
        if profile_id is not None:
            args["profile_id"] = profile_id
        return _coerce_object(self.call("create_contact", args))

    def create_draft(
        self,
        account_id: int,
        to_recipients: str,
        subject: str,
        body_text: str,
        cc_recipients: str = "",
        bcc_recipients: str = "",
        in_reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        return _coerce_object(
            self.call(
                "create_draft",
                {
                    "account_id": account_id,
                    "to_recipients": to_recipients,
                    "subject": subject,
                    "body_text": body_text,
                    "cc_recipients": cc_recipients,
                    "bcc_recipients": bcc_recipients,
                    "in_reply_to_message_id": in_reply_to_message_id,
                },
            )
        )

    def send_draft(self, draft_id: int, automation_consent_id: int | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"draft_id": draft_id, "interactive_ok": False}
        if automation_consent_id is not None:
            args["automation_consent_id"] = automation_consent_id
        return _coerce_object(self.call("send_draft", args))

    def create_forward_draft(
        self,
        message_id: int,
        to_recipients: str,
        note: str = "",
        cc_recipients: str = "",
        bcc_recipients: str = "",
        subject: str = "",
        attachment_indices: list[int] | None = None,
        attachment_filenames: list[str] | None = None,
        include_attachments: bool = False,
    ) -> dict[str, Any]:
        return _coerce_object(
            self.call(
                "create_forward_draft",
                {
                    "message_id": message_id,
                    "to_recipients": to_recipients,
                    "note": note,
                    "cc_recipients": cc_recipients,
                    "bcc_recipients": bcc_recipients,
                    "subject": subject,
                    "attachment_indices": attachment_indices or [],
                    "attachment_filenames": attachment_filenames or [],
                    "include_attachments": include_attachments,
                },
            )
        )

    def list_calendar_events(self, account_id: int, start_at: str, end_at: str, limit: int = 50) -> dict[str, Any]:
        return _coerce_object(
            self.call(
                "list_calendar_events",
                {
                    "account_id": account_id,
                    "start_at": start_at,
                    "end_at": end_at,
                    "limit": limit,
                },
            )
        )

    def create_calendar_event(
        self,
        account_id: int,
        title: str,
        starts_at: str,
        ends_at: str = "",
        location: str = "",
        description: str = "",
        attendees: str = "",
        profile_id: int | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "account_id": account_id,
            "title": title,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "location": location,
            "description": description,
            "attendees": attendees,
        }
        if profile_id is not None:
            args["profile_id"] = profile_id
        return _coerce_object(self.call("create_calendar_event", args))


def _coerce_records(raw: Any) -> list[dict[str, Any]]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        parsed = json.loads(raw)
    else:
        parsed = raw
    if isinstance(parsed, dict) and "result" in parsed:
        parsed = parsed["result"]
    if parsed is None:
        return []
    if isinstance(parsed, list):
        records = parsed
    else:
        records = [parsed]
    result: list[dict[str, Any]] = []
    for item in records:
        if isinstance(item, str):
            item = json.loads(item)
        if isinstance(item, dict) and "result" in item and len(item) == 1:
            nested = item["result"]
            if isinstance(nested, list):
                result.extend(record for record in nested if isinstance(record, dict))
                continue
        if isinstance(item, dict):
            result.append(item)
    return result


def _coerce_object(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        parsed = json.loads(raw)
    else:
        parsed = raw
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}
