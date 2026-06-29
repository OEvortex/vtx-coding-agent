from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Account:
    user_id: str
    channel: str
    display_name: str = ""
    role: str = "user"  # user, admin
    metadata: dict[str, Any] = field(default_factory=dict)


class AccountManager:
    def __init__(self, store_path: Path | None = None) -> None:
        self._store_path = store_path
        self._accounts: dict[str, Account] = {}
        self._load()

    def _load(self) -> None:
        if self._store_path and self._store_path.exists():
            data = json.loads(self._store_path.read_text())
            for uid, info in data.get("accounts", {}).items():
                self._accounts[uid] = Account(user_id=uid, **info)

    def _save(self) -> None:
        if self._store_path:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            out = {}
            for uid, acc in self._accounts.items():
                out[uid] = {
                    "channel": acc.channel,
                    "display_name": acc.display_name,
                    "role": acc.role,
                    "metadata": acc.metadata,
                }
            self._store_path.write_text(json.dumps({"accounts": out}, indent=2))

    def get_or_create(self, user_id: str, channel: str, display_name: str = "") -> Account:
        if user_id not in self._accounts:
            self._accounts[user_id] = Account(
                user_id=user_id, channel=channel, display_name=display_name
            )
            self._save()
        return self._accounts[user_id]

    def get(self, user_id: str) -> Account | None:
        return self._accounts.get(user_id)

    def list_accounts(self) -> list[Account]:
        return list(self._accounts.values())
