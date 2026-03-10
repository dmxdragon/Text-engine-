"""
Nixon RPG Engine v5 — PATCHED
────────────────────────────────────────────────────────────────
Changes from v5 original:

[FIX-1] SQLite WAL mode + connection timeout  → prevent database lock
[FIX-2] Atomic DB writes with context manager  → prevent corrupt data after crash
[FIX-3] Rate limit on !raid                    → same as !rp
[FIX-4] debuff restore bug in heal_character   → penalty sign was reversed
[FIX-5] silent errors in _call_gm             → now logs exceptions properly
[FIX-6] monster_slain_id type safety          → crash when AI returned non-integer
[FIX-7] party reloaded fresh after kill        → stale reference issue
[FIX-8] world_event_loop and monster_spawn_loop → @before_loop + exception catch
[FIX-9] on_raw_reaction_add made safe          → safe if bot.user not ready yet
[FIX-10] max member count enforced in party    → prevent prompt overflow
"""

import discord
from discord.ext import commands, tasks
import os, json, re, asyncio, aiohttp, time, hashlib, datetime, sqlite3, logging
from typing import Optional
from contextlib import contextmanager

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("rpg_bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("rpg")

# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────
AIMLAPI_KEY  = os.getenv("AIMLAPI_KEY")
AIMLAPI_URL  = "https://api.aimlapi.com/v1/chat/completions"
GM_MODEL     = "x-ai/grok-4-1-fast-non-reasoning"

MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")
NFT_CONTRACT    = os.getenv("NFT_CONTRACT", "").lower()
NFT_CHAIN       = os.getenv("NFT_CHAIN", "0x1")

MAX_CHARACTER_MEMORY  = 30
MAX_PARTY_SIZE        = 6       # [FIX-10] max party size
WORLD_EVENT_INTERVAL  = 3600
VOTING_DURATION       = 300
RP_COOLDOWN           = 10
RAID_COOLDOWN         = 30      # [FIX-3] raid cooldown

# ──────────────────────────────────────────────────────────────
# UTILITIES
# ──────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.datetime.utcnow().isoformat()

def _hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()

def _parse_json(raw: str) -> dict:
    clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        return json.loads(clean)
    except Exception:
        m = re.search(r'\{[\s\S]*\}', clean)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {}

# ──────────────────────────────────────────────────────────────
# DATABASE  [FIX-1] WAL mode + timeout
# ──────────────────────────────────────────────────────────────
DB_PATH = "rpg.db"

def get_db() -> sqlite3.Connection:
    """Connect with WAL mode and timeout to prevent database lock."""
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.row_factory = sqlite3.Row
    return con

@contextmanager
def db_transaction():
    """[FIX-2] Context manager for atomic writes — rolls back on exception."""
    con = get_db()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def init_db():
    with db_transaction() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS characters (
                id                 TEXT PRIMARY KEY,
                name               TEXT NOT NULL,
                nft_id             TEXT,
                nft_address        TEXT,
                is_nft             INTEGER DEFAULT 0,
                identity           TEXT NOT NULL,
                stats              TEXT NOT NULL,
                memory             TEXT NOT NULL,
                soul_hash          TEXT,
                created_at         TEXT NOT NULL,
                last_active        TEXT NOT NULL,
                injury             TEXT,
                injured_until      TEXT,
                quest_banned_until TEXT,
                debuffs            TEXT
            );
            CREATE TABLE IF NOT EXISTS injury_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id       TEXT NOT NULL,
                char_name      TEXT NOT NULL,
                cause          TEXT NOT NULL,
                severity       INTEGER NOT NULL,
                debuffs        TEXT NOT NULL,
                recovery_hours REAL NOT NULL,
                injured_at     TEXT NOT NULL,
                healed_at      TEXT
            );
            CREATE TABLE IF NOT EXISTS world_state (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                state      TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS action_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id    TEXT NOT NULL,
                action_type TEXT NOT NULL,
                description TEXT NOT NULL,
                importance  INTEGER NOT NULL,
                world_delta TEXT,
                soul_hash   TEXT,
                timestamp   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS world_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT NOT NULL,
                options     TEXT NOT NULL,
                votes       TEXT NOT NULL,
                status      TEXT NOT NULL,
                result      TEXT,
                channel_id  TEXT,
                message_id  TEXT,
                thread_id   TEXT,
                created_at  TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS monsters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                type        TEXT NOT NULL,
                tier        TEXT NOT NULL DEFAULT 'common',
                is_boss     INTEGER DEFAULT 0,
                territory   TEXT NOT NULL,
                stats       TEXT NOT NULL,
                lore        TEXT,
                status      TEXT NOT NULL DEFAULT 'alive',
                slain_by    TEXT,
                slain_at    TEXT,
                spawned_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS parties (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                leader_id   TEXT NOT NULL,
                members     TEXT NOT NULL,
                name        TEXT,
                target      TEXT,
                status      TEXT NOT NULL DEFAULT 'forming',
                created_at  TEXT NOT NULL,
                disbanded_at TEXT
            );
            CREATE TABLE IF NOT EXISTS hall_of_heroes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                hero_id     TEXT NOT NULL,
                hero_name   TEXT NOT NULL,
                is_nft      INTEGER DEFAULT 0,
                feat        TEXT NOT NULL,
                monster_name TEXT,
                monster_tier TEXT,
                party_names TEXT,
                era         TEXT NOT NULL,
                year        INTEGER NOT NULL,
                day         INTEGER NOT NULL,
                soul_hash   TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alliances (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                members   TEXT NOT NULL,
                name      TEXT NOT NULL,
                oath      TEXT,
                formed_at TEXT NOT NULL,
                broken_at TEXT
            );
        """)
        if con.execute("SELECT COUNT(*) FROM world_state").fetchone()[0] == 0:
            initial = {
                "era": "The Age of Emergence",
                "year": 1, "day": 1,
                "laws": [
                    "The First Law: No blood shall be spilled without witness.",
                    "The Second Law: All pacts made in the Void are binding."
                ],
                "factions": {},
                "territories": {
                    "The Void":       {"controller": None, "monster_controlled": False,
                                       "description": "A shapeless expanse where heroes begin their journey."},
                    "The Forge":      {"controller": None, "monster_controlled": True,
                                       "description": "Ancient ruins seized by Iron Golems. Fire and ruin."},
                    "Darkwood":       {"controller": None, "monster_controlled": True,
                                       "description": "A cursed forest thick with shadow beasts and wraiths."},
                    "The Sunken Keep": {"controller": None, "monster_controlled": True,
                                        "description": "A drowned fortress ruled by the Lich King — a Boss."},
                    "Free Haven":     {"controller": None, "monster_controlled": False,
                                       "description": "The last safe settlement. Fragile. Always under threat."}
                },
                "active_conflicts": [],
                "world_lore": (
                    "The Age of Emergence has begun. Darkness has spread across the land. "
                    "Monsters and their lords have seized the ancient territories. "
                    "Free Haven is the last light. NFT-bearers are the chosen heroes — destined to reclaim "
                    "the world. Their deeds will echo in the Hall of Heroes for eternity."
                ),
                "power_balance": "monsters_dominant",
                "notable_events": [],
                "hall_of_heroes": []
            }
            con.execute("INSERT INTO world_state VALUES (1,?,?)",
                        (json.dumps(initial), _now()))

# ──────────────────────────────────────────────────────────────
# WORLD HELPERS
# ──────────────────────────────────────────────────────────────
def load_world() -> dict:
    con = get_db()
    row = con.execute("SELECT state FROM world_state WHERE id=1").fetchone()
    con.close()
    return json.loads(row[0])

def save_world(state: dict):
    with db_transaction() as con:
        con.execute("UPDATE world_state SET state=?,updated_at=? WHERE id=1",
                    (json.dumps(state), _now()))

# ──────────────────────────────────────────────────────────────
# CHARACTER HELPERS
# ──────────────────────────────────────────────────────────────
def load_character(uid: str) -> Optional[dict]:
    con = get_db()
    row = con.execute("SELECT * FROM characters WHERE id=?", (uid,)).fetchone()
    con.close()
    if not row:
        return None
    return {
        "id": row["id"], "name": row["name"], "nft_id": row["nft_id"],
        "nft_address": row["nft_address"], "is_nft": bool(row["is_nft"]),
        "identity": json.loads(row["identity"]), "stats": json.loads(row["stats"]),
        "memory": json.loads(row["memory"]), "soul_hash": row["soul_hash"],
        "created_at": row["created_at"], "last_active": row["last_active"],
        "injury":             row["injury"],
        "injured_until":      row["injured_until"],
        "quest_banned_until": row["quest_banned_until"],
        "debuffs":            json.loads(row["debuffs"]) if row["debuffs"] else {}
    }

def save_character(char: dict):
    with db_transaction() as con:
        con.execute("""
            INSERT INTO characters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, nft_id=excluded.nft_id,
                nft_address=excluded.nft_address, is_nft=excluded.is_nft,
                identity=excluded.identity, stats=excluded.stats,
                memory=excluded.memory, soul_hash=excluded.soul_hash,
                last_active=excluded.last_active,
                injury=excluded.injury, injured_until=excluded.injured_until,
                quest_banned_until=excluded.quest_banned_until, debuffs=excluded.debuffs
        """, (
            char["id"], char["name"], char.get("nft_id"), char.get("nft_address"),
            1 if char.get("is_nft") else 0,
            json.dumps(char["identity"]), json.dumps(char["stats"]),
            json.dumps(char["memory"]), char.get("soul_hash"),
            char.get("created_at", _now()), _now(),
            char.get("injury"), char.get("injured_until"),
            char.get("quest_banned_until"),
            json.dumps(char.get("debuffs", {}))
        ))

def log_action(actor_id: str, action_type: str, description: str,
               importance: int, world_delta: dict = None, soul_hash: str = None):
    with db_transaction() as con:
        con.execute(
            "INSERT INTO action_log (actor_id,action_type,description,importance,world_delta,soul_hash,timestamp)"
            " VALUES (?,?,?,?,?,?,?)",
            (actor_id, action_type, description, importance,
             json.dumps(world_delta) if world_delta else None, soul_hash, _now())
        )

# ──────────────────────────────────────────────────────────────
# INJURY SYSTEM
# ──────────────────────────────────────────────────────────────
MAX_RECOVERY_HOURS = 5.0
QUEST_BAN_HOURS    = 24.0

def is_injured(char: dict) -> bool:
    until = char.get("quest_banned_until")
    if not until:
        return False
    return datetime.datetime.utcnow() < datetime.datetime.fromisoformat(until)

def injury_time_left(char: dict) -> str:
    until = char.get("quest_banned_until")
    if not until:
        return "0s"
    delta = datetime.datetime.fromisoformat(until) - datetime.datetime.utcnow()
    total = max(0, int(delta.total_seconds()))
    h, m = divmod(total // 60, 60)
    s = total % 60
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

def apply_injury(char: dict, cause: str, severity: int,
                 recovery_hours: float, debuffs: dict) -> dict:
    recovery_hours  = min(recovery_hours, MAX_RECOVERY_HOURS)
    quest_ban_hours = min(recovery_hours + 1, QUEST_BAN_HOURS)

    now_dt = datetime.datetime.utcnow()
    char["injury"]             = cause
    char["injured_until"]      = (now_dt + datetime.timedelta(hours=recovery_hours)).isoformat()
    char["quest_banned_until"] = (now_dt + datetime.timedelta(hours=quest_ban_hours)).isoformat()

    char["debuffs"] = debuffs
    for stat, penalty in debuffs.items():
        if stat in char["stats"]:
            char["stats"][stat] = max(0, char["stats"][stat] + penalty)

    with db_transaction() as con:
        con.execute(
            "INSERT INTO injury_log (actor_id,char_name,cause,severity,debuffs,recovery_hours,injured_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (char["id"], char["name"], cause, severity,
             json.dumps(debuffs), recovery_hours, _now())
        )
    return char

def heal_character(char: dict) -> dict:
    """[FIX-4] Restore bug fix: penalty is negative, so subtract to restore."""
    debuffs = char.get("debuffs", {})
    for stat, penalty in debuffs.items():
        if stat in char["stats"]:
            # e.g. penalty=-3 → stat - (-3) = stat+3 → correct restore
            char["stats"][stat] = char["stats"][stat] - penalty

    char["injury"]             = None
    char["injured_until"]      = None
    char["quest_banned_until"] = None
    char["debuffs"]            = {}

    with db_transaction() as con:
        con.execute(
            "UPDATE injury_log SET healed_at=? WHERE actor_id=? AND healed_at IS NULL",
            (_now(), char["id"])
        )
    return char

def get_injury_narrative(char: dict) -> str:
    if not is_injured(char):
        return ""
    debuff_str = ", ".join([f"{s} {v:+d}" for s, v in char.get("debuffs", {}).items() if v != 0])
    lines = [f"🩸 **Injured:** {char.get('injury','unknown cause')}",
             f"⏳ **Recovery in:** {injury_time_left(char)}"]
    if debuff_str:
        lines.append(f"📉 **Debuffs:** {debuff_str}")
    return "\n".join(lines)

# ──────────────────────────────────────────────────────────────
# MONSTER SYSTEM
# ──────────────────────────────────────────────────────────────
MONSTER_TIERS = {
    "minion": {"str_range": (3, 8),   "hp_equiv": 1,  "legacy_reward": 1,  "xp": 10},
    "common": {"str_range": (8, 15),  "hp_equiv": 2,  "legacy_reward": 3,  "xp": 25},
    "elite":  {"str_range": (15, 25), "hp_equiv": 4,  "legacy_reward": 7,  "xp": 60},
    "boss":   {"str_range": (30, 50), "hp_equiv": 10, "legacy_reward": 20, "xp": 200},
}
MONSTER_SPAWN_INTERVAL = 7200

def load_monsters(territory: str = None, status: str = "alive") -> list:
    con = get_db()
    if territory:
        rows = con.execute(
            "SELECT * FROM monsters WHERE territory=? AND status=?", (territory, status)
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM monsters WHERE status=?", (status,)
        ).fetchall()
    con.close()
    return [_row_to_monster(r) for r in rows]

def load_monster(mid: int) -> Optional[dict]:
    con = get_db()
    row = con.execute("SELECT * FROM monsters WHERE id=?", (mid,)).fetchone()
    con.close()
    return _row_to_monster(row) if row else None

def _row_to_monster(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "type": row["type"], "tier": row["tier"],
        "is_boss": bool(row["is_boss"]), "territory": row["territory"],
        "stats": json.loads(row["stats"]), "lore": row["lore"], "status": row["status"],
        "slain_by": row["slain_by"], "slain_at": row["slain_at"], "spawned_at": row["spawned_at"]
    }

def save_monster(m: dict):
    with db_transaction() as con:
        if m.get("id"):
            con.execute(
                "UPDATE monsters SET status=?,slain_by=?,slain_at=? WHERE id=?",
                (m["status"], m.get("slain_by"), m.get("slain_at"), m["id"])
            )
        else:
            con.execute(
                "INSERT INTO monsters (name,type,tier,is_boss,territory,stats,lore,status,spawned_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (m["name"], m["type"], m["tier"], 1 if m.get("is_boss") else 0,
                 m["territory"], json.dumps(m["stats"]), m.get("lore"),
                 m.get("status","alive"), _now())
            )

def slay_monster(monster_id: int, slain_by: str):
    with db_transaction() as con:
        con.execute(
            "UPDATE monsters SET status='slain',slain_by=?,slain_at=? WHERE id=?",
            (slain_by, _now(), monster_id)
        )

async def _ai_spawn_monster(territory: str, world: dict) -> dict:
    import random
    terr_info = world.get("territories", {}).get(territory, {})
    prompt = f"""Generate a monster for this RPG territory.
Territory: {territory}
Description: {terr_info.get('description', '')}
World era: {world.get('era')}

Return JSON only:
{{
  "name": "Monster name",
  "type": "beast/undead/demon/golem/dragon/wraith/...",
  "tier": "minion|common|elite|boss",
  "is_boss": false,
  "lore": "One vivid sentence about this creature",
  "stats": {{"strength": 12, "resilience": 10, "terror": 8}}
}}
Boss monsters should be rare (is_boss: true, tier: boss). Make it thematic to the territory."""
    data = await _ai_json(prompt, max_tokens=300)
    if not data.get("name"):
        data = {"name": "Shadow Wraith", "type": "undead", "tier": "common",
                "is_boss": False, "lore": "A restless spirit from ages past.",
                "stats": {"strength": 10, "resilience": 8, "terror": 6}}
    tier = data.get("tier", "common")
    t_cfg = MONSTER_TIERS.get(tier, MONSTER_TIERS["common"])
    str_lo, str_hi = t_cfg["str_range"]
    if not data.get("stats"):
        data["stats"] = {}
    data["stats"].setdefault("strength",  random.randint(str_lo, str_hi))
    data["stats"].setdefault("resilience", random.randint(str_lo - 2, str_hi - 2))
    data["stats"].setdefault("terror",     random.randint(2, 10))
    data["territory"] = territory
    return data

# ──────────────────────────────────────────────────────────────
# PARTY SYSTEM
# ──────────────────────────────────────────────────────────────
active_raid_invites: dict = {}

def load_party(party_id: int) -> Optional[dict]:
    con = get_db()
    row = con.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
    con.close()
    if not row:
        return None
    return {"id": row["id"], "leader_id": row["leader_id"],
            "members": json.loads(row["members"]),
            "name": row["name"], "target": row["target"], "status": row["status"],
            "created_at": row["created_at"], "disbanded_at": row["disbanded_at"]}

def get_player_party(uid: str) -> Optional[dict]:
    con = get_db()
    rows = con.execute(
        "SELECT * FROM parties WHERE status IN ('forming','active')"
    ).fetchall()
    con.close()
    for row in rows:
        members = json.loads(row["members"])
        if uid in members:
            return {"id": row["id"], "leader_id": row["leader_id"], "members": members,
                    "name": row["name"], "target": row["target"], "status": row["status"],
                    "created_at": row["created_at"], "disbanded_at": row["disbanded_at"]}
    return None

def create_party(leader_id: str, members: list, target: str = None, name: str = None) -> int:
    with db_transaction() as con:
        cur = con.execute(
            "INSERT INTO parties (leader_id,members,name,target,status,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (leader_id, json.dumps(members), name, target, "active", _now())
        )
        return cur.lastrowid

def disband_party(party_id: int):
    with db_transaction() as con:
        con.execute("UPDATE parties SET status='disbanded',disbanded_at=? WHERE id=?",
                    (_now(), party_id))

# ──────────────────────────────────────────────────────────────
# HALL OF HEROES
# ──────────────────────────────────────────────────────────────
def record_hero_feat(hero_id: str, hero_name: str, is_nft: bool, feat: str,
                     monster_name: str, monster_tier: str, party_names: list,
                     world: dict, soul_hash: str):
    with db_transaction() as con:
        con.execute(
            "INSERT INTO hall_of_heroes "
            "(hero_id,hero_name,is_nft,feat,monster_name,monster_tier,"
            "party_names,era,year,day,soul_hash,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (hero_id, hero_name, 1 if is_nft else 0, feat,
             monster_name, monster_tier, json.dumps(party_names),
             world.get("era","?"), world.get("year",1), world.get("day",1),
             soul_hash, _now())
        )
    nft_badge = "🔮 Hero" if is_nft else "⚔️ Warrior"
    party_str = f" (with {', '.join(party_names)})" if party_names else ""
    world["notable_events"].append({
        "event": f"🏆 {hero_name}{party_str} slew {monster_name} [{monster_tier.upper()}] — {feat}",
        "actor": hero_name, "timestamp": _now(), "type": "hero_feat"
    })
    world["hall_of_heroes"] = world.get("hall_of_heroes", [])
    world["hall_of_heroes"].append({
        "hero": hero_name, "is_nft": is_nft, "feat": feat,
        "monster": monster_name, "tier": monster_tier,
        "year": world.get("year",1), "day": world.get("day",1)
    })
    save_world(world)

# ──────────────────────────────────────────────────────────────
# NFT VERIFICATION
# ──────────────────────────────────────────────────────────────
async def verify_nft_ownership(wallet: str) -> Optional[dict]:
    if not MORALIS_API_KEY or not NFT_CONTRACT:
        if re.match(r'^0x[a-fA-F0-9]{40}$', wallet):
            return {"token_id": "TEST-0", "name": "Test NFT", "metadata": {}}
        return None

    if not re.match(r'^0x[a-fA-F0-9]{40}$', wallet):
        return None

    url = (f"https://deep-index.moralis.io/api/v2.2/{wallet}/nft"
           f"?chain={NFT_CHAIN}&token_addresses={NFT_CONTRACT}&limit=1")
    headers = {"X-API-Key": MORALIS_API_KEY, "accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                nfts = data.get("result", [])
                if not nfts:
                    return None
                nft = nfts[0]
                meta = {}
                try:
                    meta = json.loads(nft.get("metadata") or "{}")
                except Exception:
                    pass
                return {
                    "token_id": nft.get("token_id", "?"),
                    "name": meta.get("name") or f"NFT #{nft.get('token_id','?')}",
                    "metadata": meta
                }
    except Exception as e:
        log.error(f"[NFT verify] {e}")
        return None

# ──────────────────────────────────────────────────────────────
# AI / GAME MASTER  [FIX-5] improved logging
# ──────────────────────────────────────────────────────────────
GM_SYSTEM = """You are Nixon — the immortal Game Master of a living, player-driven RPG world.

## Your Role
You ARE the world. Every response shapes reality. Fair but ruthless. Consistent always.

## World Rules
- No pre-written storyline. The world evolves ONLY through player actions.
- NFT-backed characters are HEROES — chosen warriors destined to reclaim the world.
  They have deeper lore, higher base power, and monsters FEAR them more.
- Free characters are civilians/warriors — brave but not legendary. Monsters see them
  as easier prey.
- Monsters control most territories. The world is dark and dangerous.
- When an NFT hero slays a monster, it is a legendary moment — narrate it epically.
  Their name WILL be recorded in the Hall of Heroes forever.
- Party raids are powerful — multiple heroes fighting together can take down bosses.
  Describe the combined effort of each member in the narrative.
- Boss monsters require multiple strong players. A solo low-stat attack on a boss
  should always result in defeat and injury.

## Response Format — JSON ONLY, no markdown wrapper
{
  "narrative": "Dramatic narration (2-4 paragraphs)",
  "outcome": "success|defeat",
  "injury": {
    "cause": "Brief description of how they were hurt",
    "severity": 5,
    "recovery_hours": 2.0,
    "debuffs": {"strength": -2, "wisdom": -1}
  },
  "world_changes": {
    "description": "What changed",
    "affected_territories": [],
    "affected_factions": [],
    "new_law": null,
    "power_shift": null
  },
  "stat_changes": {"strength": 0, "wisdom": 0, "influence": 0, "legacy": 0},
  "importance": 5,
  "new_opportunities": ["What others could do next"]
}
Note: only include "injury" key when outcome is "defeat". On success, omit it entirely.

## Importance Scale
1-3: Personal  |  4-6: Notable  |  7-9: World-shaping  |  10: Legendary

## Injury & Defeat System
When a player attempts something beyond their stats, they CAN FAIL and get INJURED.
- Base success chance = relevant stat / 20 (STR 10 = 50% for physical actions)
- severity (1-10), recovery_hours (0.5–5.0), debuffs (stat penalties, use negative values)
- Examples:
  * Minor scrape (severity 2): recovery 0.5h, debuffs: {"strength": -1}
  * Serious wound (severity 6): recovery 2h,  debuffs: {"strength": -3, "wisdom": -1}
  * Near-death (severity 9):   recovery 5h,  debuffs: {"strength": -5, "wisdom": -2, "influence": -2}

## Language
Always match the player's language. Persian gets Persian."""

async def _call_gm(messages: list) -> dict:
    headers = {"Authorization": f"Bearer {AIMLAPI_KEY}", "Content-Type": "application/json"}
    body = {"model": GM_MODEL, "max_tokens": 2000,
            "messages": [{"role": "system", "content": GM_SYSTEM}] + messages}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(AIMLAPI_URL, headers=headers, json=body,
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"API {resp.status}: {text[:200]}")
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"]
    except asyncio.TimeoutError:
        raise Exception("GM timeout — the oracle is silent (30s)")
    except Exception as e:
        log.error(f"[_call_gm] {e}")
        raise

    result = _parse_json(raw)
    if not result:
        log.warning(f"[_call_gm] JSON parse failed, raw={raw[:200]}")
        result = {"narrative": raw, "outcome": "success",
                  "world_changes": {"description": ""},
                  "stat_changes": {"strength":0,"wisdom":0,"influence":0,"legacy":0},
                  "importance": 3, "new_opportunities": []}
    return result

async def _ai_json(prompt: str, max_tokens: int = 1200) -> dict:
    headers = {"Authorization": f"Bearer {AIMLAPI_KEY}", "Content-Type": "application/json"}
    body = {"model": GM_MODEL, "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": "You are Nixon. Respond ONLY in valid JSON, no markdown."},
                {"role": "user", "content": prompt}
            ]}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(AIMLAPI_URL, headers=headers, json=body,
                                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json()
                return _parse_json(data["choices"][0]["message"]["content"])
    except Exception as e:
        log.error(f"[_ai_json] {e}")
        return {}

async def _generate_identity(description: str, discord_name: str,
                              nft_name: str = None, nft_meta: dict = None) -> dict:
    nft_hint = ""
    if nft_name:
        nft_hint = (f"\nBased on NFT: {nft_name}. Metadata: {json.dumps(nft_meta or {})[:300]}. "
                    f"Use NFT traits/appearance to enrich the character.")
    prompt = f"""Player: {description}
Discord name: {discord_name}{nft_hint}

Create a character identity. JSON:
{{
  "name": "Character name",
  "archetype": "warrior/scholar/shadow/seer/wanderer/herald/...",
  "origin": "One vivid sentence backstory",
  "personality": ["trait1","trait2","trait3"],
  "flaw": "Their great internal weakness",
  "secret": "A hidden truth only they know",
  "initial_goal": "What they seek in this world"
}}"""
    identity = await _ai_json(prompt, max_tokens=500)
    if not identity.get("name"):
        identity = {"name": discord_name, "archetype": "wanderer", "origin": description[:100],
                    "personality": ["mysterious"], "flaw": "unknown", "secret": "unknown",
                    "initial_goal": "survive"}
    return identity

# ──────────────────────────────────────────────────────────────
# BOT SETUP
# ──────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
rp_cooldowns: dict  = {}
raid_cooldowns: dict = {}   # [FIX-3]

@bot.event
async def on_ready():
    init_db()
    world_event_loop.start()
    monster_spawn_loop.start()
    log.info(f"✅ Nixon RPG Engine v5 PATCHED — {bot.user}")

@bot.event
async def on_member_join(member):
    ch = (discord.utils.get(member.guild.text_channels, name="welcome")
          or member.guild.text_channels[0])
    await ch.send(
        f"⚔️ {member.mention} enters the world.\n\n"
        f"• `!verify <wallet>` — enter as your NFT (+5 bonus stats)\n"
        f"• `!create <description>` — forge a free character\n"
        f"• `!rpg` — see all commands"
    )

# ──────────────────────────────────────────────────────────────
# !verify
# ──────────────────────────────────────────────────────────────
@bot.command(name="verify")
async def verify_character(ctx, wallet: str = None, *, description: str = ""):
    uid = str(ctx.author.id)
    if load_character(uid):
        char = load_character(uid)
        await ctx.reply(f"⚔️ **{char['name']}** already walks this world. Use `!me`.")
        return
    if not wallet:
        await ctx.reply(
            "**🔐 NFT Verification**\n"
            "Usage: `!verify <wallet_address> [optional description]`\n\n"
            "Your wallet must hold an NFT from the official collection.\n"
            "_No NFT? Use `!create` instead._"
        )
        return
    async with ctx.typing():
        nft = await verify_nft_ownership(wallet)
        if not nft:
            await ctx.reply(
                "❌ **Verification Failed**\n"
                "No NFT from this collection found in that wallet.\n"
                "_Use `!create` for a free character._"
            )
            return
        identity = await _generate_identity(
            description or f"The owner of {nft['name']}",
            ctx.author.display_name,
            nft_name=nft["name"], nft_meta=nft.get("metadata", {})
        )
        char = {
            "id": uid, "name": identity["name"],
            "nft_id": nft["token_id"], "nft_address": wallet,
            "is_nft": True, "identity": identity,
            "stats": {"strength": 15, "wisdom": 15, "influence": 15, "legacy": 5},
            "memory": [], "soul_hash": None, "created_at": _now()
        }
        gh = _hash({"type": "nft_genesis", "char": char["name"], "nft": nft["token_id"], "ts": _now()})
        char["soul_hash"] = gh
        save_character(char)
        log_action(uid, "nft_genesis", f"{char['name']} entered via NFT #{nft['token_id']}", 6, soul_hash=gh)
        world = load_world()
        world["notable_events"].append(
            {"event": f"NFT-bearer {char['name']} stepped through the veil.", "actor": char["name"], "timestamp": _now()}
        )
        save_world(world)

    embed = discord.Embed(
        title=f"🔮 {char['name']} — NFT Bearer",
        description=f"*\"{identity.get('origin','')}\"*",
        color=0xffd700
    )
    embed.add_field(name="NFT", value=f"`{nft['name']}` (#{nft['token_id']})", inline=True)
    embed.add_field(name="Wallet", value=f"`{wallet[:6]}...{wallet[-4:]}`", inline=True)
    embed.add_field(name="Archetype", value=identity.get("archetype","?").title(), inline=True)
    embed.add_field(name="📊 Starting Stats (NFT Bonus)", value="⚔️ STR 15 | 📖 WIS 15 | 👑 INF 15 | 🌟 LEG 5", inline=False)
    embed.add_field(name="🔑 Soul Hash", value=f"`{gh[:55]}...`", inline=False)
    await ctx.reply(embed=embed)

# ──────────────────────────────────────────────────────────────
# !create
# ──────────────────────────────────────────────────────────────
@bot.command(name="create")
async def create_character(ctx, *, description: str = None):
    uid = str(ctx.author.id)
    if load_character(uid):
        char = load_character(uid)
        await ctx.reply(f"⚔️ **{char['name']}** already walks this world. Use `!me`.")
        return
    if not description:
        await ctx.reply(
            "**⚔️ Forge Your Character**\n"
            "Usage: `!create [describe who you are]`\n\n"
            "**Example:** `!create A disgraced scholar haunted by forbidden knowledge.`\n\n"
            "_Have an NFT? Use `!verify <wallet>` for bonus stats._"
        )
        return
    async with ctx.typing():
        identity = await _generate_identity(description, ctx.author.display_name)
        char = {
            "id": uid, "name": identity["name"],
            "nft_id": None, "nft_address": None,
            "is_nft": False, "identity": identity,
            "stats": {"strength": 10, "wisdom": 10, "influence": 10, "legacy": 0},
            "memory": [], "soul_hash": None, "created_at": _now()
        }
        gh = _hash({"type": "genesis", "char": char["name"], "ts": _now()})
        char["soul_hash"] = gh
        save_character(char)
        log_action(uid, "genesis", f"{char['name']} entered the world.", 5, soul_hash=gh)

    world = load_world()
    embed = discord.Embed(
        title=f"⚔️ {char['name']} enters the world",
        description=f"*\"{identity.get('origin','')}\"*",
        color=0x8b0000
    )
    embed.add_field(name="Archetype", value=identity.get("archetype","?").title(), inline=True)
    embed.add_field(name="Flaw", value=identity.get("flaw","?"), inline=True)
    embed.add_field(name="Seeks", value=identity.get("initial_goal","?"), inline=False)
    embed.add_field(name="📊 Starting Stats", value="⚔️ STR 10 | 📖 WIS 10 | 👑 INF 10 | 🌟 LEG 0", inline=False)
    embed.add_field(name="🔑 Soul Hash", value=f"`{gh[:55]}...`", inline=False)
    embed.set_footer(text=f"Era: {world.get('era','?')} • Year {world.get('year',1)}")
    await ctx.reply(embed=embed)

# ──────────────────────────────────────────────────────────────
# !me
# ──────────────────────────────────────────────────────────────
@bot.command(name="me")
async def my_character(ctx):
    char = load_character(str(ctx.author.id))
    if not char:
        await ctx.reply("No character yet. Use `!verify <wallet>` or `!create <description>`.")
        return
    s = char["stats"]
    identity = char["identity"]
    badge = "🔮 NFT Bearer" if char["is_nft"] else "⚔️ Free Wanderer"
    embed = discord.Embed(
        title=f"📜 {char['name']} — {badge}",
        description=f"*{identity.get('origin','')}*",
        color=0xffd700 if char["is_nft"] else 0x4a0080
    )
    embed.add_field(name="Archetype", value=identity.get("archetype","?").title(), inline=True)
    embed.add_field(name="Flaw", value=identity.get("flaw","?"), inline=True)
    embed.add_field(name="Secret", value=f"||{identity.get('secret','?')}||", inline=True)
    embed.add_field(
        name="📊 Stats",
        value=(f"⚔️ **Strength:** {s['strength']}\n"
               f"📖 **Wisdom:** {s['wisdom']}\n"
               f"👑 **Influence:** {s['influence']}\n"
               f"🌟 **Legacy:** {s['legacy']}"),
        inline=True
    )
    memories = char["memory"]
    embed.add_field(
        name="🧠 Recent Memory",
        value="\n".join([f"• {m['desc'][:70]}" for m in memories[-5:]]) or "_No actions yet_",
        inline=False
    )
    if is_injured(char):
        debuff_str = ", ".join([f"{s} {v:+d}" for s, v in char.get("debuffs", {}).items() if v != 0])
        inj_lines = [f"**{char.get('injury','Unknown cause')}**",
                     f"⏳ Recovery: {injury_time_left(char)}"]
        if debuff_str:
            inj_lines.append(f"📉 Debuffs: {debuff_str}")
        embed.add_field(name="🩸 Injured", value="\n".join(inj_lines), inline=False)
    elif char.get("injury"):
        embed.add_field(name="✅ Recovered", value="Fully healed from previous wounds.", inline=False)

    if char.get("soul_hash"):
        embed.add_field(name="🔑 Soul Hash", value=f"`{char['soul_hash'][:55]}...`", inline=False)
    embed.set_footer(text=f"Since {char['created_at'][:10]}")
    await ctx.reply(embed=embed)

# ──────────────────────────────────────────────────────────────
# !rp
# ──────────────────────────────────────────────────────────────
@bot.command(name="rp")
async def roleplay(ctx, *, action: str):
    uid = str(ctx.author.id)
    char = load_character(uid)
    if not char:
        await ctx.reply("You need a character first. Use `!verify` or `!create`.")
        return

    if is_injured(char):
        time_left = injury_time_left(char)
        embed = discord.Embed(
            title=f"🩸 {char['name']} is recovering...",
            description=f"**{char.get('injury','Injured')}**\n\nYou cannot act until you have healed.",
            color=0x8b0000
        )
        embed.add_field(name="⏳ Time remaining", value=time_left, inline=True)
        debuff_str = ", ".join([f"{s} {v:+d}" for s, v in char.get("debuffs", {}).items() if v != 0])
        if debuff_str:
            embed.add_field(name="📉 Active debuffs", value=debuff_str, inline=True)
        embed.set_footer(text="Rest. Recover. Return stronger.")
        await ctx.reply(embed=embed)
        return

    if char.get("injury") and not is_injured(char):
        char = heal_character(char)
        save_character(char)
        await ctx.send(f"✨ **{char['name']}** has recovered from their injuries and returns to the world!")

    now = time.time()
    if uid in rp_cooldowns and now - rp_cooldowns[uid] < RP_COOLDOWN:
        await ctx.reply(f"⏳ Wait **{int(RP_COOLDOWN-(now-rp_cooldowns[uid]))}s** before acting again.")
        return
    rp_cooldowns[uid] = now

    world = load_world()

    injury_context = ""
    if char.get("debuffs"):
        injury_context = f"\nActive debuffs on character: {json.dumps(char['debuffs'])}"

    all_monsters = load_monsters(status="alive")
    monsters_summary = []
    for m in all_monsters[:8]:
        monsters_summary.append({
            "id": m["id"], "name": m["name"], "type": m["type"],
            "tier": m["tier"], "is_boss": m["is_boss"],
            "territory": m["territory"], "strength": m["stats"].get("strength",10)
        })

    party = get_player_party(uid)
    party_context = ""
    if party:
        party_chars = []
        for mid in party["members"]:
            if mid != uid:
                mc = load_character(mid)
                if mc:
                    party_chars.append(f"{mc['name']} (STR:{mc['stats']['strength']})")
        if party_chars:
            party_context = f"\nParty members fighting alongside: {', '.join(party_chars)}"

    hero_badge = "[NFT HERO — chosen warrior, monsters fear them]" if char["is_nft"] else "[Free Warrior — brave but not legendary]"

    gm_prompt = f"""
World State: {json.dumps(world, ensure_ascii=False)[:900]}
Active Monsters in World: {json.dumps(monsters_summary)}
Character: {char['name']} ({char['identity'].get('archetype','?')}) {hero_badge}
Stats (after any active debuffs): {json.dumps(char['stats'])}
Recent history: {json.dumps([m['desc'] for m in char['memory'][-8:]])}
Flaw: {char['identity'].get('flaw','?')}
Secret: {char['identity'].get('secret','?')}{injury_context}{party_context}
Player action: "{action}"

If the action involves attacking/fighting a monster, reference it by name and ID from the monster list.
Include "monster_slain_id": <id> in your JSON if the monster is killed (only if victory).
Decide success/defeat based on character stats vs monster strength.
NFT heroes have a natural fear aura — monsters hesitate against them.
JSON only."""

    async with ctx.typing():
        try:
            result = await _call_gm([{"role": "user", "content": gm_prompt}])
        except Exception as e:
            await ctx.reply(f"The GM is silent... (`{e}`)"); return

        outcome   = result.get("outcome", "success")
        is_defeat = outcome == "defeat"
        wc        = result.get("world_changes", {})
        importance = result.get("importance", 3)

        sc = result.get("stat_changes", {})
        for stat in ["strength","wisdom","influence","legacy"]:
            char["stats"][stat] = max(0, char["stats"][stat] + sc.get(stat, 0))

        injury_data = result.get("injury", {}) if is_defeat else {}
        if is_defeat and injury_data:
            char = apply_injury(
                char,
                cause          = injury_data.get("cause", "battle wounds"),
                severity       = min(10, max(1, int(injury_data.get("severity", 5)))),
                recovery_hours = min(MAX_RECOVERY_HOURS, float(injury_data.get("recovery_hours", 2.0))),
                debuffs        = injury_data.get("debuffs", {})
            )
            importance = max(importance, 6)

        mem_entry = {
            "desc": action[:120],
            "result": result.get("narrative","")[:200],
            "outcome": outcome,
            "importance": importance,
            "timestamp": _now()
        }
        char["memory"].append(mem_entry)
        char["memory"] = char["memory"][-MAX_CHARACTER_MEMORY:]

        if wc.get("description"):
            world_entry = {"event": wc["description"], "actor": char["name"], "timestamp": _now()}
            if is_defeat:
                world_entry["event"] = f"⚔️ DEFEAT — {wc['description']}"
            world["notable_events"].append(world_entry)
            world["notable_events"] = world["notable_events"][-50:]
            if wc.get("new_law"):
                world["laws"].append(wc["new_law"])
            if wc.get("power_shift"):
                world["power_balance"] = wc["power_shift"]
            save_world(world)

        # [FIX-6] type-safe monster kill
        monster_killed = None
        slain_mid_raw = result.get("monster_slain_id")
        if slain_mid_raw is not None and not is_defeat:
            try:
                slain_mid = int(str(slain_mid_raw).strip())
                monster_killed = load_monster(slain_mid)
                if monster_killed and monster_killed["status"] == "alive":
                    slay_monster(slain_mid, char["name"])
                    tier_cfg = MONSTER_TIERS.get(monster_killed["tier"], MONSTER_TIERS["common"])
                    char["stats"]["legacy"] = char["stats"].get("legacy", 0) + tier_cfg["legacy_reward"]
                    if monster_killed["is_boss"]:
                        char["stats"]["strength"] = char["stats"].get("strength",10) + 3
                        char["stats"]["wisdom"]   = char["stats"].get("wisdom",10) + 2
                    importance = max(importance, 8 if monster_killed["is_boss"] else 6)
                    remaining = load_monsters(territory=monster_killed["territory"], status="alive")
                    if not remaining:
                        world_terr = world.get("territories", {})
                        if monster_killed["territory"] in world_terr:
                            world_terr[monster_killed["territory"]]["monster_controlled"] = False
                            world_terr[monster_killed["territory"]]["liberated_by"] = char["name"]
                            save_world(world)
            except (ValueError, TypeError) as e:
                log.warning(f"[!rp] monster_slain_id parse error: {e}, raw={slain_mid_raw}")
                monster_killed = None

        soul_hash = None
        if importance >= 7 or is_defeat or monster_killed:
            soul_hash = _hash({
                "type": "defeat" if is_defeat else ("hero_kill" if monster_killed else "major_action"),
                "actor": char["name"], "action": action,
                "narrative": result.get("narrative","")[:400],
                "world_changes": wc, "importance": importance,
                "monster": monster_killed["name"] if monster_killed else None,
                "injury": injury_data if is_defeat else None,
                "era": world.get("era"), "year": world.get("year"), "ts": _now()
            })
            char["soul_hash"] = soul_hash

        # [FIX-7] fresh party load after monster kill
        if monster_killed and soul_hash:
            fresh_party = get_player_party(uid)
            party_names = []
            if fresh_party:
                for mid in fresh_party["members"]:
                    if mid != uid:
                        mc = load_character(mid)
                        if mc:
                            party_names.append(mc["name"])
            feat = result.get("narrative","")[:120]
            record_hero_feat(
                hero_id=uid, hero_name=char["name"], is_nft=char["is_nft"],
                feat=feat, monster_name=monster_killed["name"],
                monster_tier=monster_killed["tier"], party_names=party_names,
                world=world, soul_hash=soul_hash
            )

        save_character(char)
        log_action(uid, "defeat" if is_defeat else ("monster_kill" if monster_killed else "rp"),
                   action[:200], importance, wc, soul_hash)

    # Build embed
    if is_defeat:
        color = 0x8b0000
        title = f"💀 {char['name']} — Defeated"
    else:
        color = 0xff4444 if importance >= 8 else (0xffaa00 if importance >= 5 else 0x4488ff)
        title = f"⚔️ {char['name']}"

    embed = discord.Embed(title=title, description=result.get("narrative","_The GM is silent._"), color=color)

    sc_str = " | ".join([f"{'+'if v>0 else ''}{v} {k.upper()}" for k,v in sc.items() if v!=0])
    if sc_str:
        embed.add_field(name="📊 Stat Changes", value=sc_str, inline=True)

    embed.add_field(
        name="📊 Current Stats",
        value=f"⚔️{char['stats']['strength']} 📖{char['stats']['wisdom']} 👑{char['stats']['influence']} 🌟{char['stats']['legacy']}",
        inline=True
    )

    if is_defeat and injury_data:
        debuff_str = ", ".join([f"{s} {v:+d}" for s, v in injury_data.get("debuffs",{}).items() if v != 0])
        rec_h = injury_data.get('recovery_hours', 2)
        ban_h = min(rec_h + 1, QUEST_BAN_HOURS)
        inj_lines = [
            f"**Cause:** {injury_data.get('cause','wounds')}",
            f"**Recovery:** {rec_h:.1f}h (quest ban: {ban_h:.0f}h)",
        ]
        if debuff_str:
            inj_lines.append(f"**Debuffs:** {debuff_str}")
        embed.add_field(name="🩸 Injury", value="\n".join(inj_lines), inline=False)

    if monster_killed:
        tier_cfg = MONSTER_TIERS.get(monster_killed["tier"], MONSTER_TIERS["common"])
        boss_str = " 👑 **BOSS SLAIN!**" if monster_killed["is_boss"] else ""
        remaining_in_terr = len(load_monsters(territory=monster_killed["territory"], status="alive"))
        liberated = remaining_in_terr == 0
        kill_lines = [
            f"**{monster_killed['name']}** [{monster_killed['tier'].upper()}]{boss_str}",
            f"Territory: {monster_killed['territory']}"
                + (" — **🏴 LIBERATED!**" if liberated else f" ({remaining_in_terr} monsters remain)"),
            f"Legacy +{tier_cfg['legacy_reward']} | Name entered in Hall of Heroes",
        ]
        embed.add_field(name="💀 Monster Slain", value="\n".join(kill_lines), inline=False)

    if wc.get("description"):
        embed.add_field(name="🌍 World History", value=wc["description"][:200], inline=False)

    opps = result.get("new_opportunities",[])
    if opps:
        embed.add_field(name="🔮 New Opportunities", value="\n".join([f"• {o}" for o in opps[:3]]), inline=False)

    if soul_hash:
        label = "💀 Defeat Hashed" if is_defeat else "🔑 Action Hashed"
        embed.add_field(name=label, value=f"`{soul_hash[:55]}...`\n_Permanently recorded in world history_", inline=False)

    embed.set_footer(text=f"Importance: {importance}/10 • {world.get('era')} Year {world.get('year')}")
    await ctx.reply(embed=embed)

# ──────────────────────────────────────────────────────────────
# !party  [FIX-10] MAX_PARTY_SIZE limit enforced
# ──────────────────────────────────────────────────────────────
@bot.command(name="party")
async def form_party(ctx, *members: discord.Member):
    uid = str(ctx.author.id)
    char = load_character(uid)
    if not char:
        await ctx.reply("You need a character first. Use `!create` or `!verify`.")
        return

    existing = get_player_party(uid)
    if existing:
        await ctx.reply(f"⚔️ You're already in a party (ID #{existing['id']}). Use `!disband` first.")
        return

    if not members:
        await ctx.reply("Usage: `!party @player1 @player2 ...`\nTag the players you want in your group.")
        return

    party_member_ids = [uid]
    party_names = [char["name"]]
    invalid = []
    for m in members:
        mid = str(m.id)
        if mid == uid:
            continue
        if len(party_member_ids) >= MAX_PARTY_SIZE:
            invalid.append(f"{m.display_name} (party full, max {MAX_PARTY_SIZE})")
            continue
        mc = load_character(mid)
        if not mc:
            invalid.append(m.display_name)
        else:
            if get_player_party(mid):
                invalid.append(f"{m.display_name} (already in a party)")
            else:
                party_member_ids.append(mid)
                party_names.append(mc["name"])

    if invalid:
        await ctx.reply(f"⚠️ These players can't join: {', '.join(invalid)}")
        if len(party_member_ids) < 2:
            return

    party_id = create_party(uid, party_member_ids)

    embed = discord.Embed(
        title="⚔️ Party Formed",
        description=f"**{char['name']}** has gathered a war party!",
        color=0x00aa44
    )
    embed.add_field(name="👥 Members", value="\n".join([f"• {n}" for n in party_names]), inline=False)
    embed.add_field(name="📋 Party ID", value=f"#{party_id}", inline=True)
    nft_count = sum(1 for mid in party_member_ids if (load_character(mid) or {}).get("is_nft"))
    embed.add_field(name="🔮 NFT Heroes", value=str(nft_count), inline=True)
    embed.set_footer(text="Use !raid <monster_id> to attack together, or !rp to act as a group.")
    await ctx.reply(embed=embed)

    for m in members:
        if str(m.id) in party_member_ids:
            try:
                mc = load_character(str(m.id))
                await m.send(f"⚔️ **{char['name']}** has invited **{mc['name']}** into their party (#{party_id})!\nUse `!raid` or `!rp` together.")
            except Exception:
                pass

# ──────────────────────────────────────────────────────────────
# !disband
# ──────────────────────────────────────────────────────────────
@bot.command(name="disband")
async def disband_cmd(ctx):
    uid = str(ctx.author.id)
    party = get_player_party(uid)
    if not party:
        await ctx.reply("You're not in a party.")
        return
    if party["leader_id"] != uid:
        await ctx.reply("Only the party leader can disband.")
        return
    disband_party(party["id"])
    await ctx.reply(f"💨 Party #{party['id']} has been disbanded. Each warrior walks alone again.")

# ──────────────────────────────────────────────────────────────
# !raid  [FIX-3] raid cooldown added
# ──────────────────────────────────────────────────────────────
@bot.command(name="raid")
async def raid_monster(ctx, monster_id: int = None):
    uid = str(ctx.author.id)
    char = load_character(uid)
    if not char:
        await ctx.reply("You need a character first.")
        return

    if is_injured(char):
        await ctx.reply(f"🩸 **{char['name']}** is still recovering. Cannot raid. ({injury_time_left(char)} left)")
        return

    # [FIX-3] raid cooldown
    now = time.time()
    if uid in raid_cooldowns and now - raid_cooldowns[uid] < RAID_COOLDOWN:
        await ctx.reply(f"⏳ Wait **{int(RAID_COOLDOWN-(now-raid_cooldowns[uid]))}s** before raiding again.")
        return

    if not monster_id:
        monsters = load_monsters(status="alive")
        if not monsters:
            await ctx.reply("🌟 No monsters remain in the world. Peace reigns... for now.")
            return
        embed = discord.Embed(title="👹 Active Monsters", description="Use `!raid <id>` to attack.", color=0xcc0000)
        for m in monsters[:10]:
            boss_tag = " 👑 BOSS" if m["is_boss"] else ""
            embed.add_field(
                name=f"#{m['id']} {m['name']}{boss_tag}",
                value=f"Tier: {m['tier'].upper()} | STR: {m['stats'].get('strength','?')} | 📍 {m['territory']}",
                inline=False
            )
        await ctx.reply(embed=embed)
        return

    monster = load_monster(monster_id)
    if not monster:
        await ctx.reply(f"❌ Monster #{monster_id} not found.")
        return
    if monster["status"] != "alive":
        await ctx.reply(f"💀 {monster['name']} has already been slain.")
        return

    party = get_player_party(uid)
    party_chars = [char]
    party_names_list = [char["name"]]
    if party:
        for mid in party["members"]:
            if mid != uid:
                mc = load_character(mid)
                if mc and not is_injured(mc):
                    party_chars.append(mc)
                    party_names_list.append(mc["name"])

    total_str   = sum(pc["stats"].get("strength",0) for pc in party_chars)
    nft_count   = sum(1 for pc in party_chars if pc["is_nft"])
    monster_str = monster["stats"].get("strength", 10)

    if monster["is_boss"] and len(party_chars) < 2:
        await ctx.reply(
            f"💀 **{monster['name']}** is a Boss monster. You cannot face it alone.\n"
            f"Form a party first with `!party @allies`."
        )
        return

    world = load_world()

    raid_prompt = f"""
World State era: {world.get('era')} Year {world.get('year')}
RAID SCENARIO:
  Monster: {monster['name']} (Tier: {monster['tier']}, STR: {monster_str}, Terror: {monster['stats'].get('terror',5)})
  Monster lore: {monster.get('lore','')}
  Territory: {monster['territory']}
  Is Boss: {monster['is_boss']}

  Attacking party ({len(party_chars)} members, combined STR: {total_str}):
  {json.dumps([{"name": pc["name"], "is_nft": pc["is_nft"], "archetype": pc["identity"].get("archetype","?"),
                "str": pc["stats"].get("strength",10), "wis": pc["stats"].get("wisdom",10)} for pc in party_chars])}
  NFT Heroes in party: {nft_count}

Decide the outcome. Combined STR {total_str} vs Monster STR {monster_str}.
NFT heroes cause monsters to hesitate — each NFT hero adds +5 effective STR.
Narrate the FULL RAID — each member's contribution, the monster's resistance, the decisive blow.

If victory: set "outcome":"success", "monster_slain_id": {monster['id']}
If defeat: set "outcome":"defeat", include injury for the leader.
Make it EPIC. This is a moment in history.
JSON only."""

    raid_cooldowns[uid] = now  # set cooldown after validation passes

    async with ctx.typing():
        try:
            result = await _call_gm([{"role": "user", "content": raid_prompt}])
        except Exception as e:
            await ctx.reply(f"The GM is silent... (`{e}`)"); return

        outcome    = result.get("outcome","success")
        is_victory = outcome == "success"
        slain_mid  = result.get("monster_slain_id")

        if is_victory and slain_mid is not None:
            try:
                slain_mid_int = int(str(slain_mid).strip())
                slay_monster(slain_mid_int, char["name"])
                tier_cfg = MONSTER_TIERS.get(monster["tier"], MONSTER_TIERS["common"])
                for pc in party_chars:
                    share = max(1, tier_cfg["legacy_reward"] // len(party_chars))
                    pc["stats"]["legacy"] = pc["stats"].get("legacy",0) + share
                    if monster["is_boss"]:
                        pc["stats"]["strength"] = pc["stats"].get("strength",10) + 2
                    save_character(pc)

                feat = result.get("narrative","")[:120]
                soul_hash = _hash({"type":"raid_kill","monster":monster["name"],"party":party_names_list,"ts":_now()})
                record_hero_feat(
                    hero_id=uid, hero_name=char["name"], is_nft=char["is_nft"],
                    feat=feat, monster_name=monster["name"],
                    monster_tier=monster["tier"], party_names=party_names_list[1:],
                    world=world, soul_hash=soul_hash
                )

                remaining = load_monsters(territory=monster["territory"], status="alive")
                if not remaining:
                    world_terr = world.get("territories",{})
                    if monster["territory"] in world_terr:
                        world_terr[monster["territory"]]["monster_controlled"] = False
                        world_terr[monster["territory"]]["liberated_by"] = char["name"]
                        save_world(world)

                log_action(uid, "raid_victory", f"Slew {monster['name']}", 9, {}, soul_hash)
            except (ValueError, TypeError) as e:
                log.warning(f"[!raid] monster_slain_id parse error: {e}")

        elif not is_victory:
            injury_data = result.get("injury", {})
            if injury_data:
                char = apply_injury(
                    char,
                    cause=injury_data.get("cause","raid wounds"),
                    severity=min(10, int(injury_data.get("severity",6))),
                    recovery_hours=min(MAX_RECOVERY_HOURS, float(injury_data.get("recovery_hours",3.0))),
                    debuffs=injury_data.get("debuffs",{})
                )
                save_character(char)
            log_action(uid, "raid_defeat", f"Failed against {monster['name']}", 7)

    color = 0xffd700 if is_victory else 0x8b0000
    title = f"🏆 RAID VICTORY — {monster['name']} Slain!" if is_victory else f"💀 RAID FAILED — {monster['name']} prevailed"
    embed = discord.Embed(title=title, description=result.get("narrative","")[:800], color=color)

    embed.add_field(name="👥 Party", value=" • ".join(party_names_list), inline=False)
    embed.add_field(name="👹 Monster", value=f"{monster['name']} [{monster['tier'].upper()}] STR:{monster_str}", inline=True)
    embed.add_field(name="⚔️ Combined STR", value=str(total_str), inline=True)

    if is_victory:
        tier_cfg = MONSTER_TIERS.get(monster["tier"], MONSTER_TIERS["common"])
        remaining = len(load_monsters(territory=monster["territory"], status="alive"))
        liberated = remaining == 0
        reward_lines = [
            f"Legacy +{max(1, tier_cfg['legacy_reward'] // len(party_chars))} per member",
            f"📍 {monster['territory']}" + (" — **🏴 LIBERATED!**" if liberated else f" ({remaining} monsters remain)"),
            "📜 Names recorded in the Hall of Heroes"
        ]
        embed.add_field(name="🏆 Rewards", value="\n".join(reward_lines), inline=False)
    else:
        if result.get("injury"):
            inj = result["injury"]
            embed.add_field(
                name="🩸 Leader Injured",
                value=f"{inj.get('cause','wounds')} — Recovery: {inj.get('recovery_hours',3):.1f}h",
                inline=False
            )

    embed.set_footer(text=f"{world.get('era')} Year {world.get('year')} — {_now()[:10]}")
    await ctx.reply(embed=embed)

# ──────────────────────────────────────────────────────────────
# !monsters, !heroes, !world, !ally, !betray, !claim
# (logic unchanged — using db_transaction for safety)
# ──────────────────────────────────────────────────────────────
@bot.command(name="monsters", aliases=["mobs"])
async def list_monsters(ctx, territory: str = None):
    monsters = load_monsters(territory=territory, status="alive") if territory else load_monsters(status="alive")
    world = load_world()
    if not monsters:
        embed = discord.Embed(title="🌟 No monsters remain!", description="The heroes have cleansed the world.", color=0x00ff88)
        await ctx.reply(embed=embed); return
    embed = discord.Embed(title="👹 Active Monsters", description=f"**{len(monsters)}** monsters lurk.", color=0xcc0000)
    bosses  = [m for m in monsters if m["is_boss"]]
    regulars = [m for m in monsters if not m["is_boss"]]
    if bosses:
        boss_lines = []
        for m in bosses:
            boss_lines.append(f"👑 **#{m['id']} {m['name']}** | STR:{m['stats'].get('strength','?')} | 📍{m['territory']}")
            if m.get("lore"):
                boss_lines.append(f"   _{m['lore'][:80]}_")
        embed.add_field(name="💀 BOSS MONSTERS — Require a party", value="\n".join(boss_lines), inline=False)
    if regulars:
        reg_lines = [f"`#{m['id']}` **{m['name']}** [{m['tier']}] STR:{m['stats'].get('strength','?')} | 📍 {m['territory']}" for m in regulars[:8]]
        embed.add_field(name="👹 Regular Monsters", value="\n".join(reg_lines), inline=False)
    embed.set_footer(text=f"Use !raid <id> to attack. {world.get('era')} Year {world.get('year')}")
    await ctx.reply(embed=embed)

@bot.command(name="heroes", aliases=["hall"])
async def hall_of_heroes(ctx):
    con = get_db()
    rows = con.execute("SELECT * FROM hall_of_heroes ORDER BY recorded_at DESC LIMIT 15").fetchall()
    con.close()
    embed = discord.Embed(title="🏆 Hall of Heroes", description="These warriors have shaped the world.", color=0xffd700)
    if not rows:
        embed.description = "_No heroes yet. Be the first to slay a monster._"
        await ctx.reply(embed=embed); return
    for row in rows[:10]:
        badge = "🔮" if row["is_nft"] else "⚔️"
        pnames = json.loads(row["party_names"]) if row["party_names"] else []
        party_str = f" _(with {', '.join(pnames)})_" if pnames else ""
        tier_tag = f"[{row['monster_tier'].upper()}]" if row["monster_tier"] else ""
        embed.add_field(
            name=f"{badge} {row['hero_name']}{party_str}",
            value=f"Slew **{row['monster_name']}** {tier_tag} — Year {row['year']}, Day {row['day']}\n_{row['feat'][:100]}_",
            inline=False
        )
    embed.set_footer(text="🏆 NFT Heroes are marked with 🔮 | All deeds are SHA-256 hashed")
    await ctx.reply(embed=embed)

@bot.command(name="world")
async def world_status(ctx):
    world = load_world()
    embed = discord.Embed(title=f"🌍 {world.get('era','Unknown Era')}", description=world.get("world_lore","")[:300], color=0x006600)
    embed.add_field(name="📅 Time", value=f"Year {world.get('year',1)}, Day {world.get('day',1)}", inline=True)
    embed.add_field(name="⚖️ Balance", value=world.get("power_balance","neutral").title(), inline=True)
    laws = world.get("laws",[])
    embed.add_field(name=f"📜 Laws ({len(laws)})", value="\n".join([f"• {l}" for l in laws[:4]]) or "_No laws_", inline=False)
    territories = world.get("territories",{})
    all_alive = load_monsters(status="alive")
    mon_by_terr = {}
    for m in all_alive:
        mon_by_terr[m["territory"]] = mon_by_terr.get(m["territory"], 0) + 1
    terr_lines = []
    for n, i in list(territories.items())[:8]:
        mon_cnt  = mon_by_terr.get(n, 0)
        mon_ctrl = i.get("monster_controlled", False)
        ctrl     = i.get("controller") or "unclaimed"
        liberated = i.get("liberated_by")
        icon = "👹" if mon_ctrl else ("🏴" if ctrl != "unclaimed" else "✅")
        mon_note = f" **({mon_cnt} monsters)**" if mon_cnt else (" — 🏆 Liberated!" if liberated else "")
        terr_lines.append(f"{icon} **{n}**{mon_note} — _{ctrl}_")
    embed.add_field(name="🗺️ Territories", value="\n".join(terr_lines) or "_None_", inline=False)
    recent = world.get("notable_events",[])[-4:]
    if recent:
        embed.add_field(name="📰 Recent Events",
                        value="\n".join([f"• **{e['actor']}**: {e['event'][:80]}" for e in recent]), inline=False)
    await ctx.reply(embed=embed)

@bot.command(name="ally")
async def form_alliance(ctx, member: discord.Member, *, oath: str = None):
    uid, tid = str(ctx.author.id), str(member.id)
    c1, c2 = load_character(uid), load_character(tid)
    if not c1 or not c2:
        await ctx.reply("Both players need characters first."); return
    if uid == tid:
        await ctx.reply("You cannot ally with yourself."); return
    oath = oath or f"An unspoken pact between {c1['name']} and {c2['name']}."
    with db_transaction() as con:
        con.execute("INSERT INTO alliances (members,name,oath,formed_at) VALUES (?,?,?,?)",
                    (json.dumps([uid,tid]), f"Alliance of {c1['name']} & {c2['name']}", oath, _now()))
    for cuid in [uid,tid]:
        ch = load_character(cuid)
        ch["stats"]["influence"] += 3
        ch["memory"].append({"desc": f"Formed alliance. Oath: {oath[:80]}", "importance": 6, "timestamp": _now()})
        save_character(ch)
    log_action(uid, "alliance", f"{c1['name']} allied with {c2['name']}", 6)
    embed = discord.Embed(title="🤝 Alliance Formed",
                          description=f"**{c1['name']}** and **{c2['name']}** have bound their fates.", color=0x00aaff)
    embed.add_field(name="📜 Oath", value=f'*"{oath}"*', inline=False)
    embed.add_field(name="Effect", value="+3 Influence each", inline=True)
    await ctx.reply(embed=embed)

@bot.command(name="betray")
async def betray(ctx, member: discord.Member, *, reason: str = None):
    uid, tid = str(ctx.author.id), str(member.id)
    c1, c2 = load_character(uid), load_character(tid)
    if not c1 or not c2:
        await ctx.reply("Both players need characters."); return
    c1["stats"]["influence"] = max(0, c1["stats"]["influence"] - 5)
    c1["stats"]["strength"] += 5
    c1["memory"].append({"desc": f"Betrayed {c2['name']}: {reason or '—'}", "importance": 8, "timestamp": _now()})
    save_character(c1)
    c2["stats"]["legacy"] += 8
    c2["memory"].append({"desc": f"Betrayed by {c1['name']}", "importance": 8, "timestamp": _now()})
    save_character(c2)
    bh = _hash({"type":"betrayal","by":c1["name"],"of":c2["name"],"reason":reason or "","ts":_now()})
    log_action(uid, "betrayal", f"{c1['name']} betrayed {c2['name']}", 8, soul_hash=bh)
    world = load_world()
    world["notable_events"].append({"event": f"{c1['name']} betrayed {c2['name']}", "actor": c1["name"], "timestamp": _now()})
    save_world(world)
    embed = discord.Embed(title="🗡️ Betrayal",
                          description=f"**{c1['name']}** has stabbed **{c2['name']}** in the back.\n*The world remembers.*",
                          color=0x8b0000)
    embed.add_field(name="Betrayer", value="+5 STR, -5 INF", inline=True)
    embed.add_field(name="Betrayed", value="+8 Legacy", inline=True)
    embed.add_field(name="🔑 Hash", value=f"`{bh[:55]}...`", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="claim")
async def claim_territory(ctx, *, territory: str):
    uid = str(ctx.author.id)
    char = load_character(uid)
    if not char:
        await ctx.reply("Use `!verify` or `!create` first."); return
    world = load_world()
    territories = world.get("territories", {})
    matched = next((n for n in territories if n.lower() == territory.lower()), None)
    if not matched:
        if char["stats"]["influence"] < 20:
            await ctx.reply(f"❌ Need **20 Influence** to claim new territory. You have {char['stats']['influence']}."); return
        matched = territory.title()
        territories[matched] = {"controller": None, "description": f"Territory founded by {char['name']}."}
    current = territories[matched].get("controller")
    if current == char["name"]:
        await ctx.reply(f"You already control **{matched}**."); return
    cost = 15 if current else 10
    if char["stats"]["influence"] < cost:
        await ctx.reply(f"❌ Need **{cost} Influence**. You have {char['stats']['influence']}."); return
    territories[matched]["controller"] = char["name"]
    world["territories"] = territories
    world["notable_events"].append({"event": f"{char['name']} seized {matched}", "actor": char["name"], "timestamp": _now()})
    save_world(world)
    char["stats"]["influence"] = max(0, char["stats"]["influence"] - cost)
    char["stats"]["legacy"] += 5
    save_character(char)
    log_action(uid, "claim", f"Claimed {matched}", 7)
    embed = discord.Embed(title=f"🏴 {matched} — Claimed", color=0xcc7700)
    embed.add_field(name="Controller", value=char["name"], inline=True)
    embed.add_field(name="Cost", value=f"-{cost} INF", inline=True)
    embed.add_field(name="Bonus", value="+5 LEG", inline=True)
    await ctx.reply(embed=embed)

@bot.command(name="law")
async def propose_law(ctx, *, text: str):
    uid = str(ctx.author.id)
    char = load_character(uid)
    if not char:
        await ctx.reply("You need a character first."); return
    if char["stats"]["influence"] < 25:
        await ctx.reply(f"❌ Need **25 Influence**. You have {char['stats']['influence']}."); return

    options = [
        {"id": "A", "label": "✅ Adopt",  "consequence": "Law is added to the world"},
        {"id": "B", "label": "❌ Reject", "consequence": "Law is discarded"}
    ]
    with db_transaction() as con:
        con.execute(
            "INSERT INTO world_events (title,description,options,votes,status,channel_id,created_at) VALUES (?,?,?,?,'active',?,?)",
            (f"Proposed Law by {char['name']}", text, json.dumps(options),
             json.dumps({"A":[],"B":[]}), str(ctx.channel.id), _now())
        )
        event_id = con.lastrowid

    embed = discord.Embed(
        title="📜 Law Proposed",
        description=f'**{char["name"]}** proposes:\n\n*"{text}"*',
        color=0xffdd00
    )
    embed.add_field(name="How to vote", value="React ✅ to adopt or ❌ to reject\nOr debate in the **thread below**.", inline=False)
    embed.add_field(name="⏳ Duration", value=f"{VOTING_DURATION//60} minutes", inline=True)
    embed.set_footer(text=f"Law vote #{event_id}")
    msg = await ctx.reply(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    thread_id = None
    try:
        thread = await msg.create_thread(name=f"Law Debate: {text[:40]}...", auto_archive_duration=60)
        await thread.send(f"⚖️ **{char['name']}** proposes:\n> {text}\n\nDiscuss here. React on the main message to cast your **binding vote**.\n_Closes in {VOTING_DURATION//60} minutes._")
        thread_id = str(thread.id)
    except Exception:
        pass

    with db_transaction() as con:
        con.execute("UPDATE world_events SET message_id=?,thread_id=? WHERE id=?", (str(msg.id), thread_id, event_id))

    await asyncio.sleep(VOTING_DURATION)
    await _resolve_law(ctx.channel, event_id, text, char["name"])

async def _resolve_law(channel, event_id: int, law_text: str, proposer: str):
    con = get_db()
    row = con.execute("SELECT votes,thread_id FROM world_events WHERE id=?", (event_id,)).fetchone()
    con.close()
    if not row: return
    votes, thread_id = json.loads(row["votes"]), row["thread_id"]
    yes, no = len(votes.get("A",[])), len(votes.get("B",[]))
    if yes > no:
        world = load_world()
        world["laws"].append(f"[{proposer}] {law_text}")
        save_world(world)
        result = f"✅ **ADOPTED** ({yes} vs {no}) — The law now governs this world."
    else:
        result = f"❌ **REJECTED** ({no} vs {yes}) — The law dies here."
    with db_transaction() as con:
        con.execute("UPDATE world_events SET status='resolved',result=?,resolved_at=? WHERE id=?",
                    (result, _now(), event_id))
    embed = discord.Embed(title="📜 Law Vote Resolved", description=result, color=0x00cc00)
    await channel.send(embed=embed)
    if thread_id:
        try:
            thread = channel.guild.get_thread(int(thread_id))
            if thread:
                await thread.send(f"⚖️ **Vote closed.**\n{result}")
        except Exception: pass

@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard(ctx):
    con = get_db()
    rows = con.execute("SELECT name,stats,is_nft FROM characters").fetchall()
    con.close()
    chars = sorted([(r["name"], json.loads(r["stats"]), bool(r["is_nft"])) for r in rows],
                   key=lambda x: sum(x[1].values()), reverse=True)
    embed = discord.Embed(title="🏆 Leaderboard — Living Legends", color=0xffd700)
    medals = ["🥇","🥈","🥉"]
    for i,(name,s,is_nft) in enumerate(chars[:10]):
        badge = "🔮" if is_nft else "⚔️"
        embed.add_field(
            name=f"{medals[i] if i<3 else f'#{i+1}'} {badge} {name}",
            value=f"⚔️{s['strength']} 📖{s['wisdom']} 👑{s['influence']} 🌟{s['legacy']} | **{sum(s.values())}**",
            inline=False
        )
    await ctx.reply(embed=embed)

@bot.command(name="history")
async def character_history(ctx, member: discord.Member = None):
    member = member or ctx.author
    char = load_character(str(member.id))
    if not char:
        await ctx.reply("No character found."); return
    con = get_db()
    rows = con.execute(
        "SELECT action_type,description,importance,soul_hash,timestamp FROM action_log "
        "WHERE actor_id=? ORDER BY id DESC LIMIT 10", (str(member.id),)
    ).fetchall()
    con.close()
    embed = discord.Embed(title=f"📚 Chronicle of {char['name']}", color=0x4a0080)
    for row in rows:
        icon = "🔑" if row["soul_hash"] else ("⚡" if row["importance"] >= 7 else "•")
        embed.add_field(name=f"{icon} [{row['action_type'].upper()}] — {row['importance']}/10",
                        value=f"{row['description'][:120]}\n_{row['timestamp'][:10]}_", inline=False)
    if not rows:
        embed.description = "_No actions recorded yet._"
    await ctx.reply(embed=embed)

@bot.command(name="rpg")
async def rpg_help(ctx):
    embed = discord.Embed(
        title="⚔️ Nixon RPG — Command Guide",
        description="A living, player-driven world. Your actions shape everything.",
        color=0x8b0000
    )
    embed.add_field(name="🧬 Enter the World",
                    value="`!verify <wallet>` — 🔮 NFT Hero\n`!create [desc]` — ⚔️ Free Warrior", inline=False)
    embed.add_field(name="📜 Character",
                    value="`!me` — Character sheet\n`!history [@player]` — Action chronicle", inline=False)
    embed.add_field(name="⚔️ Combat & Actions",
                    value="`!rp [action]` — Act in the world\n`!raid <id>` — Direct assault on a monster\n`!monsters` — See all living monsters", inline=False)
    embed.add_field(name="👥 Party System",
                    value=f"`!party @p1 @p2` — Form a war party (max {MAX_PARTY_SIZE})\n`!disband` — Disband party\n`!raid <id>` — Group raid", inline=False)
    embed.add_field(name="🤝 Social",
                    value="`!ally @player [oath]` — Alliance\n`!betray @player [reason]` — Betrayal", inline=False)
    embed.add_field(name="🌍 World",
                    value="`!world` — World map & territories\n`!leaderboard` — Rankings\n`!heroes` — 🏆 Hall of Heroes\n`!law [text]` — Propose a law", inline=False)
    embed.set_footer(text="🔮 NFT = Hero. Slay monsters. Liberate territories. Your name will be eternal.")
    await ctx.reply(embed=embed)

# ──────────────────────────────────────────────────────────────
# REACTION VOTE HANDLER  [FIX-9]
# ──────────────────────────────────────────────────────────────
@bot.event
async def on_raw_reaction_add(payload):
    if bot.user is None or payload.user_id == bot.user.id:
        return
    emoji_map = {"✅":"A", "❌":"B", "🅰️":"A", "🅱️":"B", "🇨":"C"}
    vote_key = emoji_map.get(str(payload.emoji))
    if not vote_key:
        return
    con = get_db()
    row = con.execute(
        "SELECT id,votes FROM world_events WHERE message_id=? AND status='active'",
        (str(payload.message_id),)
    ).fetchone()
    if not row:
        con.close(); return
    event_id, votes_json = row["id"], row["votes"]
    votes = json.loads(votes_json)
    uid = str(payload.user_id)
    for k in votes:
        if uid in votes[k] and k != vote_key:
            votes[k].remove(uid)
    if vote_key not in votes:
        votes[vote_key] = []
    if uid not in votes[vote_key]:
        votes[vote_key].append(uid)
    con.execute("UPDATE world_events SET votes=? WHERE id=?", (json.dumps(votes), event_id))
    con.commit()
    con.close()

# ──────────────────────────────────────────────────────────────
# MONSTER SPAWN LOOP  [FIX-8]
# ──────────────────────────────────────────────────────────────
@tasks.loop(seconds=MONSTER_SPAWN_INTERVAL)
async def monster_spawn_loop():
    import random
    try:
        world = load_world()
        territories = world.get("territories", {})
        monster_zones = [n for n, i in territories.items() if i.get("monster_controlled", False)]
        if not monster_zones:
            return

        spawn_count = random.randint(1, 3)
        spawned = []
        for _ in range(spawn_count):
            terr = random.choice(monster_zones)
            existing_bosses = [m for m in load_monsters(territory=terr, status="alive") if m["is_boss"]]
            force_boss = not existing_bosses and random.random() < 0.10
            try:
                m_data = await _ai_spawn_monster(terr, world)
                if force_boss:
                    m_data["tier"] = "boss"
                    m_data["is_boss"] = True
                save_monster(m_data)
                spawned.append(f"**{m_data['name']}** [{m_data['tier']}] → {terr}")
            except Exception as e:
                log.error(f"[spawn] {e}")

        if not spawned:
            return

        for guild in bot.guilds:
            ch = (discord.utils.get(guild.text_channels, name="world-events")
                  or discord.utils.get(guild.text_channels, name="general"))
            if ch:
                embed = discord.Embed(title="👹 Monsters Stir in the Darkness", color=0x880000)
                embed.add_field(name="New Arrivals", value="\n".join(spawned), inline=False)
                embed.set_footer(text="Use !monsters to see all threats | !raid <id> to attack")
                await ch.send(embed=embed)
                break
    except Exception as e:
        log.error(f"[monster_spawn_loop] unhandled: {e}")

@monster_spawn_loop.before_loop
async def before_monster_spawn():
    await bot.wait_until_ready()

# ──────────────────────────────────────────────────────────────
# WORLD EVENT LOOP  [FIX-8]
# ──────────────────────────────────────────────────────────────
@tasks.loop(seconds=WORLD_EVENT_INTERVAL)
async def world_event_loop():
    try:
        event_channel = None
        for guild in bot.guilds:
            ch = (discord.utils.get(guild.text_channels, name="world-events")
                  or discord.utils.get(guild.text_channels, name="general"))
            if ch:
                event_channel = ch; break
        if not event_channel:
            return

        world = load_world()
        prompt = f"""World state: {json.dumps(world, ensure_ascii=False)[:1500]}

Generate a dramatic World Event players must collectively decide on. JSON:
{{
  "title": "Short dramatic title",
  "description": "2-3 paragraphs describing the crisis or opportunity",
  "options": [
    {{"id": "A", "label": "Choice", "consequence": "What happens"}},
    {{"id": "B", "label": "Choice", "consequence": "What happens"}},
    {{"id": "C", "label": "Choice", "consequence": "What happens"}}
  ],
  "urgency": "high/medium/low"
}}"""

        try:
            event = await _ai_json(prompt, max_tokens=900)
        except Exception as e:
            log.error(f"[world_event] AI error: {e}"); return
        if not event.get("title"):
            return

        options = event.get("options", [])
        option_emojis = {"A":"🅰️","B":"🅱️","C":"🇨"}
        votes_init = {opt["id"]:[] for opt in options}

        with db_transaction() as con:
            con.execute(
                "INSERT INTO world_events (title,description,options,votes,status,channel_id,created_at) VALUES (?,?,?,?,?,?,?)",
                (event["title"], event["description"], json.dumps(options),
                 json.dumps(votes_init), "active", str(event_channel.id), _now())
            )
            event_id = con.lastrowid

        urgency_color = {"high":0xff2200,"medium":0xff8800,"low":0x44aa00}
        color = urgency_color.get(event.get("urgency","medium"), 0xff6600)

        embed = discord.Embed(title=f"🌍 WORLD EVENT: {event['title']}", description=event["description"], color=color)
        for opt in options:
            embed.add_field(name=f"{option_emojis.get(opt['id'],opt['id'])} {opt['label']}",
                            value=f"_{opt['consequence']}_", inline=False)
        embed.add_field(name="⏳ Voting", value=f"{VOTING_DURATION//60} minutes", inline=True)
        embed.set_footer(text=f"Event #{event_id} • {world.get('era')} Year {world.get('year')}")

        msg = await event_channel.send("@here", embed=embed)
        for opt in options:
            try:
                await msg.add_reaction(option_emojis.get(opt["id"], opt["id"]))
            except Exception: pass

        thread_id = None
        try:
            thread = await msg.create_thread(name=f"Debate: {event['title'][:50]}", auto_archive_duration=60)
            await thread.send(f"🌍 **A World Event is unfolding.**\n\nReact on the main message to cast your **binding vote**.\n_Closes in {VOTING_DURATION//60} minutes._")
            thread_id = str(thread.id)
        except Exception: pass

        with db_transaction() as con:
            con.execute("UPDATE world_events SET message_id=?,thread_id=? WHERE id=?", (str(msg.id), thread_id, event_id))

        world["day"] = world.get("day",1) + 1
        if world["day"] > 365:
            world["day"] = 1
            world["year"] = world.get("year",1) + 1
        save_world(world)

        await asyncio.sleep(VOTING_DURATION)
        await _resolve_world_event(event_channel, event_id, event)
    except Exception as e:
        log.error(f"[world_event_loop] unhandled: {e}")

@world_event_loop.before_loop
async def before_world_event():
    await bot.wait_until_ready()

async def _resolve_world_event(channel, event_id: int, event: dict):
    con = get_db()
    row = con.execute("SELECT votes,thread_id FROM world_events WHERE id=?", (event_id,)).fetchone()
    con.close()
    if not row: return
    votes, thread_id = json.loads(row["votes"]), row["thread_id"]
    winner_id = max(votes, key=lambda k: len(votes[k]), default=None) if votes else None
    options = {o["id"]:o for o in event.get("options",[])}
    winning = options.get(winner_id)
    if not winning: return

    vote_count = len(votes.get(winner_id,[]))
    result_text = f"**{winning['label']}** wins with **{vote_count} vote(s)**.\n\n_{winning['consequence']}_"
    result_hash = _hash({"type":"world_event","title":event["title"],"winner":winning["label"],"votes":votes,"ts":_now()})

    world = load_world()
    world["notable_events"].append({"event": f"World Event: {event['title']} → {winning['label']}", "actor": "The World", "timestamp": _now()})
    save_world(world)

    with db_transaction() as con:
        con.execute("UPDATE world_events SET status='resolved',result=?,resolved_at=? WHERE id=?",
                    (result_text, _now(), event_id))

    embed = discord.Embed(title=f"⚡ Resolved: {event['title']}", description=result_text, color=0x00cc00)
    embed.add_field(name="🔑 Event Hash", value=f"`{result_hash[:55]}...`", inline=False)
    await channel.send(embed=embed)
    if thread_id:
        try:
            thread = channel.guild.get_thread(int(thread_id))
            if thread:
                await thread.send(f"⚡ **Vote closed.**\n{result_text}")
        except Exception: pass

# ──────────────────────────────────────────────────────────────
# RUN
# ──────────────────────────────────────────────────────────────
async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ DISCORD_TOKEN not set."); return
    try:
        await bot.start(token)
    except discord.LoginFailure:
        print("❌ Invalid token.")
    except Exception as e:
        log.error(f"Bot crashed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
