"""
world_engine.py
───────────────
Nixon RPG — World State Engine
Runs when a chapter ends (boss dies) and generates the new world.

Called automatically by rpg_engine_v5.py when main boss is slain.
Can also be run manually: python world_engine.py
"""

import sqlite3
import json
import os
import datetime
import aiohttp
import asyncio
import random
from dotenv import load_dotenv

load_dotenv()

DB_PATH      = os.getenv("RPG_DB_PATH", "rpg.db")
AIMLAPI_KEY  = os.getenv("AIMLAPI_KEY", "")
AIMLAPI_URL  = "https://api.aimlapi.com/v1/chat/completions"
GM_MODEL     = "gpt-4o"

# ──────────────────────────────────────────────────────────────
# BASE TERRITORIES — always exist, state changes each chapter
# ──────────────────────────────────────────────────────────────
BASE_TERRITORIES = [
    {"name": "The Void",        "x": 0.18, "y": 0.45, "type": "wasteland"},
    {"name": "Free Haven",      "x": 0.42, "y": 0.30, "type": "settlement"},
    {"name": "The Forge",       "x": 0.68, "y": 0.22, "type": "industrial"},
    {"name": "Darkwood",        "x": 0.25, "y": 0.68, "type": "forest"},
    {"name": "The Sunken Keep", "x": 0.72, "y": 0.70, "type": "ruins"},
]

# New territories that can unlock in later chapters
EXPANSION_TERRITORIES = [
    {"name": "The Ashfields",   "x": 0.55, "y": 0.55, "type": "wasteland"},
    {"name": "Crystal Depths",  "x": 0.15, "y": 0.25, "type": "magical"},
    {"name": "Iron Citadel",    "x": 0.85, "y": 0.45, "type": "fortress"},
    {"name": "The Verdant Peak", "x": 0.50, "y": 0.12, "type": "nature"},
    {"name": "Shadow Crossing", "x": 0.35, "y": 0.85, "type": "cursed"},
]

# Boss pool — each chapter gets a new boss
BOSS_POOL = [
    {"name": "The Lich King",      "tier": "boss", "str": 40, "hp": 300, "territory": "The Sunken Keep"},
    {"name": "Iron Colossus",      "tier": "boss", "str": 50, "hp": 400, "territory": "The Forge"},
    {"name": "Void Devourer",      "tier": "boss", "str": 60, "hp": 500, "territory": "The Void"},
    {"name": "The Ancient Shadow", "tier": "boss", "str": 70, "hp": 600, "territory": "Darkwood"},
    {"name": "Eternal Tyrant",     "tier": "boss", "str": 85, "hp": 800, "territory": "The Void"},
]

def _now():
    return datetime.datetime.utcnow().isoformat()

def get_db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con

# ──────────────────────────────────────────────────────────────
# READ CHAPTER HISTORY
# ──────────────────────────────────────────────────────────────
def read_chapter_data(chapter_id: int) -> dict:
    """Read all data from the chapter that just ended."""
    con = get_db()

    # Characters
    chars = con.execute("SELECT * FROM characters").fetchall()
    chars = [dict(c) for c in chars]

    # Monsters slain this chapter
    slain = con.execute(
        "SELECT * FROM monsters WHERE status='dead'"
    ).fetchall()
    slain = [dict(m) for m in slain]

    # Active laws
    laws = con.execute(
        "SELECT * FROM world_events WHERE type='law' AND status='passed'"
    ).fetchall()
    laws = [dict(l) for l in laws]

    # Alliances
    alliances = con.execute(
        "SELECT * FROM alliances WHERE disbanded_at IS NULL"
    ).fetchall()
    alliances = [dict(a) for a in alliances]

    # Cities
    try:
        cities = con.execute("SELECT * FROM cities WHERE status='active'").fetchall()
        cities = [dict(c) for c in cities]
    except:
        cities = []

    # World events
    events = con.execute(
        "SELECT * FROM world_events ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    events = [dict(e) for e in events]

    con.close()

    # Infrastructure score
    liberated = sum(1 for m in slain if m.get("territory"))
    infra = min(100,
        len(cities) * 20 +
        len(alliances) * 15 +
        liberated * 10 +
        len(laws) * 5
    )

    return {
        "chapter_id":    chapter_id,
        "characters":    chars,
        "monsters_slain":slain,
        "laws":          laws,
        "alliances":     alliances,
        "cities":        cities,
        "events":        events,
        "infra_score":   infra,
        "total_players": len(chars),
        "boss_killers":  [m.get("slain_by_name","?") for m in slain if m.get("is_boss")],
    }

# ──────────────────────────────────────────────────────────────
# AI WORLD GENERATION
# ──────────────────────────────────────────────────────────────
async def ai_generate_chapter(chapter_data: dict, next_chapter_num: int) -> dict:
    """Ask AI to write the narrative for the new chapter."""

    laws_text = "\n".join([f"- {l.get('title','')}: {l.get('description','')}"
                           for l in chapter_data["laws"]]) or "None"

    prompt = f"""You are the narrator of Nixon RPG — a living world Discord RPG.
Chapter {chapter_data['chapter_id']} has just ended.

CHAPTER SUMMARY:
- Players: {chapter_data['total_players']}
- Infrastructure Score: {chapter_data['infra_score']}/100
- Monsters slain: {len(chapter_data['monsters_slain'])}
- Boss killers: {', '.join(chapter_data['boss_killers']) or 'None'}
- Active alliances: {len(chapter_data['alliances'])}
- Active laws: {laws_text}
- Cities built: {len(chapter_data['cities'])}

WORLD CONDITION: {"DARK AGE — infrastructure was weak" if chapter_data['infra_score'] < 40 else "STABLE" if chapter_data['infra_score'] > 70 else "FRAGILE"}

Generate the opening of Chapter {next_chapter_num}. Respond in JSON only:
{{
  "chapter_name": "Chapter {next_chapter_num}: [epic subtitle]",
  "opening_narrative": "2-3 dramatic paragraphs describing how the world changed after the boss was defeated. Reference specific laws and alliances if any.",
  "world_changes": [
    "One specific change to the world",
    "Another change",
    "A third change"
  ],
  "new_threats": "One sentence about the new danger awakening",
  "dark_age_narrative": "If dark age: one sentence about the consequences. Otherwise null.",
  "tone": "hopeful|grim|chaotic|triumphant"
}}"""

    headers = {"Authorization": f"Bearer {AIMLAPI_KEY}", "Content-Type": "application/json"}
    body = {
        "model": GM_MODEL,
        "max_tokens": 800,
        "messages": [
            {"role": "system", "content": "You are Nixon, narrator of a living RPG world. Respond ONLY in valid JSON."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(AIMLAPI_URL, headers=headers, json=body,
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"]
                raw = raw.strip().lstrip("```json").rstrip("```").strip()
                return json.loads(raw)
    except Exception as e:
        print(f"[world_engine] AI error: {e}")
        return {
            "chapter_name": f"Chapter {next_chapter_num}: The Next Age",
            "opening_narrative": "The world shifts as a new chapter begins. The consequences of past battles echo through every territory.",
            "world_changes": ["The darkness recedes slightly", "New paths open to the east", "Old alliances are tested"],
            "new_threats": "A new shadow stirs in the void.",
            "dark_age_narrative": None,
            "tone": "grim"
        }

# ──────────────────────────────────────────────────────────────
# PROCEDURAL MAP GENERATION
# ──────────────────────────────────────────────────────────────
def generate_new_map(chapter_num: int, infra_score: int, laws: list) -> dict:
    """
    Generate new territory layout for the next chapter.
    - Higher chapter = more territories unlocked
    - Laws can affect territory states
    - Infra score affects how many territories are stable
    """

    territories = list(BASE_TERRITORIES)

    # Unlock expansion territories based on chapter number
    expansions_to_unlock = min(chapter_num - 1, len(EXPANSION_TERRITORIES))
    for i in range(expansions_to_unlock):
        territories.append(EXPANSION_TERRITORIES[i])

    # Apply law effects to territories
    law_effects = {}
    for law in laws:
        desc = law.get("description","").lower()
        for t in territories:
            if t["name"].lower() in desc:
                if "no monster" in desc or "safe" in desc:
                    law_effects[t["name"]] = "law_protected"
                elif "double" in desc or "bonus" in desc:
                    law_effects[t["name"]] = "law_buffed"

    # Assign initial states based on infra score
    result = {}
    for i, t in enumerate(territories):
        state = "controlled"  # default: monsters control it

        if t["name"] == "Free Haven":
            state = "liberated"  # always starts liberated
        elif infra_score > 70 and i < 3:
            state = "neutral"
        elif infra_score > 40 and i < 2:
            state = "neutral"

        # Law override
        if t["name"] in law_effects:
            if law_effects[t["name"]] == "law_protected":
                state = "law_protected"

        result[t["name"]] = {
            "x":       t["x"] + random.uniform(-0.02, 0.02),  # slight variation
            "y":       t["y"] + random.uniform(-0.02, 0.02),
            "type":    t["type"],
            "state":   state,
            "law_effect": law_effects.get(t["name"]),
            "is_new":  t in EXPANSION_TERRITORIES[:expansions_to_unlock],
        }

    return result

# ──────────────────────────────────────────────────────────────
# SPAWN NEW BOSS
# ──────────────────────────────────────────────────────────────
def spawn_next_boss(chapter_num: int, infra_score: int) -> dict:
    """
    Each chapter gets a new boss, progressively harder.
    If infra was weak, boss is even harder.
    """
    boss_index = min(chapter_num - 1, len(BOSS_POOL) - 1)
    boss_template = BOSS_POOL[boss_index].copy()

    # Scale with chapter and infra
    scale = 1.0 + (chapter_num - 1) * 0.3
    if infra_score < 40:
        scale *= 1.5  # dark age penalty

    boss_template["hp"]  = int(boss_template["hp"] * scale)
    boss_template["str"] = int(boss_template["str"] * scale)
    boss_template["max_hp"] = boss_template["hp"]
    boss_template["status"] = "alive"
    boss_template["is_boss"] = True
    boss_template["chapter"] = chapter_num

    return boss_template

# ──────────────────────────────────────────────────────────────
# CHAPTER TRANSITION — MAIN FUNCTION
# ──────────────────────────────────────────────────────────────
async def transition_to_next_chapter(completed_chapter_id: int) -> dict:
    """
    Called when main boss dies.
    Generates the new world state and saves it to DB.
    Returns the new chapter data for Discord announcement.
    """
    print(f"[world_engine] Starting chapter transition from chapter {completed_chapter_id}...")

    next_chapter_num = completed_chapter_id + 1

    # 1. Read everything that happened
    chapter_data = read_chapter_data(completed_chapter_id)
    infra_score  = chapter_data["infra_score"]
    is_dark_age  = infra_score < 40

    print(f"[world_engine] Infra score: {infra_score} | Dark age: {is_dark_age}")

    # 2. AI generates narrative
    print("[world_engine] Calling AI for chapter narrative...")
    ai_result = await ai_generate_chapter(chapter_data, next_chapter_num)

    # 3. Generate new map
    new_map = generate_new_map(next_chapter_num, infra_score, chapter_data["laws"])

    # 4. Spawn new boss
    new_boss = spawn_next_boss(next_chapter_num, infra_score)

    # 5. Save to DB
    con = get_db()
    try:
        # Close old chapter
        con.execute(
            "UPDATE chapters SET status='completed', ended_at=? WHERE id=?",
            (_now(), completed_chapter_id)
        )

        # Ensure chapters table exists
        con.execute("""
            CREATE TABLE IF NOT EXISTS chapters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                status      TEXT DEFAULT 'active',
                boss_id     INTEGER,
                narrative   TEXT,
                world_map   TEXT,
                infra_score INTEGER DEFAULT 0,
                is_dark_age INTEGER DEFAULT 0,
                started_at  TEXT,
                ended_at    TEXT
            )
        """)

        # Insert new boss
        con.execute("""
            INSERT INTO monsters (name, tier, is_boss, territory, hp, max_hp, stats, status, created_at)
            VALUES (?,?,1,?,?,?,?,?,?)
        """, (
            new_boss["name"], "boss", new_boss["territory"],
            new_boss["hp"], new_boss["max_hp"],
            json.dumps({"strength": new_boss["str"]}),
            "alive", _now()
        ))
        new_boss_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert new chapter
        con.execute("""
            INSERT INTO chapters (name, status, boss_id, narrative, world_map, infra_score, is_dark_age, started_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            ai_result["chapter_name"],
            "active",
            new_boss_id,
            json.dumps(ai_result),
            json.dumps(new_map),
            infra_score,
            1 if is_dark_age else 0,
            _now()
        ))

        con.commit()
        print(f"[world_engine] Chapter {next_chapter_num} saved to DB.")

    except Exception as e:
        print(f"[world_engine] DB error: {e}")
        con.rollback()
    finally:
        con.close()

    return {
        "chapter_num":   next_chapter_num,
        "chapter_name":  ai_result["chapter_name"],
        "narrative":     ai_result["opening_narrative"],
        "world_changes": ai_result.get("world_changes",[]),
        "new_threats":   ai_result.get("new_threats",""),
        "is_dark_age":   is_dark_age,
        "infra_score":   infra_score,
        "new_boss":      new_boss,
        "new_map":       new_map,
        "tone":          ai_result.get("tone","grim"),
    }

# ──────────────────────────────────────────────────────────────
# MANUAL RUN
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    chapter_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Manually triggering chapter transition from chapter {chapter_id}...")
    result = asyncio.run(transition_to_next_chapter(chapter_id))
    print(json.dumps(result, indent=2, ensure_ascii=False))
