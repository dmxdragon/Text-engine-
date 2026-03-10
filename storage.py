"""
core/storage.py
───────────────
RPG Bot data storage.
Only used by the engine — Nixon Bot does not need this file.
"""

import json
import os
from typing import Optional

RPG_DATA_FILE = "rpg_player_data.json"


def _load() -> dict:
    if not os.path.exists(RPG_DATA_FILE):
        return {}
    try:
        with open(RPG_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save(data: dict):
    with open(RPG_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_player(user_id: str) -> dict:
    return _load().get(str(user_id), {})

def save_player(user_id: str, data: dict):
    all_data = _load()
    all_data[str(user_id)] = data
    _save(all_data)

def get_wallet(user_id: str) -> Optional[str]:
    return get_player(user_id).get("wallet")

def save_wallet(user_id: str, address: str):
    player = get_player(user_id)
    player["wallet"] = address
    save_player(user_id, player)
