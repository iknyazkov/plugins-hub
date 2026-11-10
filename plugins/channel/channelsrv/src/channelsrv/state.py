"""Persistent chat state: the active agent choice (``state.json``).

The server is the only writer. Writes are atomic: tmp file + ``os.replace``.
A broken or missing file is not an error — start from an empty state
(agents re-register, the user just picks the agent again).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class StateStore:
    """Active agent per chat; chat keys are strings (JSON), ints on the outside."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._chats: dict[str, dict[str, str | None]] = {}

    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        chats = raw.get("chats") if isinstance(raw, dict) else None
        if isinstance(chats, dict):
            self._chats = {
                str(chat_id): {"active_agent": entry.get("active_agent")}
                for chat_id, entry in chats.items()
                if isinstance(entry, dict)
            }

    def active_agent(self, chat_id: int) -> str | None:
        return self._chats.get(str(chat_id), {}).get("active_agent")

    def note_chat(self, chat_id: int) -> None:
        """Remembers the chat (without an agent choice) — so there is somewhere to deliver."""
        key = str(chat_id)
        if key not in self._chats:
            self._chats[key] = {"active_agent": None}
            self._save()

    def set_active_agent(self, chat_id: int, agent_id: str) -> None:
        self._chats[str(chat_id)] = {"active_agent": agent_id}
        self._save()

    def chats_for_agent(self, agent_id: str) -> list[int]:
        return [
            int(chat_id)
            for chat_id, entry in self._chats.items()
            if entry.get("active_agent") == agent_id
        ]

    def chat_ids(self) -> list[int]:
        return [int(chat_id) for chat_id in self._chats]

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"chats": self._chats}, ensure_ascii=False, indent=2))
        os.replace(tmp, self._path)
