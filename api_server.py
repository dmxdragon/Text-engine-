"""
api_server.py
─────────────
Nixon RPG — World State API Server
Connects rpg.db to the web renderer (Phaser.js)

Run:
  pip install fastapi uvicorn python-dotenv --break-system-packages
  python api_server.py
"""

import sqlite3
import json
import os
import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("RPG_DB_PATH", "rpg.db")
API_PORT = int(os.getenv("API_PORT", "8000"))

app = FastAPI(
    title="Nixon RPG World API",
    description="Living World Engine — World State API",
    version="1.0.0"
)

# Allow web renderer to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Serve static files from /static folder
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_path = os.path.join(STATIC_DIR, "nixon-world.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1 style='color:#ffd700;background:#06060f;padding:40px;font-family:monospace'>Nixon RPG API Online - place nixon-world.html in /static</h1>")

# ──────────────────────────────────────────────────────────────
# DB HELPERS
# ──────────────────────────────────────────────────────────────
def get_db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con

def db_query(sql: str, params: tuple = ()) -> list:
    try:
        con = get_db()
        rows = con.execute(sql, params).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return []

def db_one(sql: str, params: tuple = ()):
    try:
        con = get_db()
        row = con.execute(sql, params).fetchone()
        con.close()
        return dict(row) if row else None
    except Exception:
        return None

# ──────────────────────────────────────────────────────────────
# WORLD STATE CALCULATOR
# ──────────────────────────────────────────────────────────────
def calculate_world_state() -> dict:
    """
    Core engine: reads all player actions from DB
    and calculates the current world state.
    """

    # ── Characters ──
    chars = db_query("SELECT * FROM characters WHERE hp > 0")
    total_chars    = len(chars)
    nft_chars      = sum(1 for c in chars if c.get("is_nft"))
    total_legacy   = sum(json.loads(c.get("stats","{}")).get("legacy",0) for c in chars)
    total_strength = sum(json.loads(c.get("stats","{}")).get("strength",0) for c in chars)

    # ── Monsters ──
    monsters_alive = db_query("SELECT * FROM monsters WHERE status='alive'")
    monsters_slain = db_query("SELECT * FROM monsters WHERE status='dead'")
    boss_alive     = any(m.get("is_boss") for m in monsters_alive)

    # ── Alliances ──
    alliances = db_query("SELECT * FROM alliances WHERE disbanded_at IS NULL")
    total_alliances = len(alliances)

    # ── Active Laws ──
    laws = db_query("SELECT * FROM world_events WHERE type='law' AND status='passed'")
    active_laws = len(laws)

    # ── Territories ──
    # Calculate per territory: monsters alive vs slain
    territory_states = {}
    all_territories = [
        "The Void", "Free Haven", "The Forge",
        "Darkwood", "The Sunken Keep"
    ]
    for t in all_territories:
        alive  = sum(1 for m in monsters_alive if m.get("territory") == t)
        dead   = sum(1 for m in monsters_slain if m.get("territory") == t)
        # characters stationed here
        heroes = [c for c in chars if json.loads(c.get("identity","{}")).get("territory","") == t]

        if alive == 0 and dead > 0:
            state = "liberated"
        elif alive > 0:
            state = "controlled"
        else:
            state = "neutral"

        territory_states[t] = {
            "state":        state,
            "monsters_alive": alive,
            "monsters_slain": dead,
            "heroes":       len(heroes),
        }

    # ── Infrastructure Score ──
    cities_count   = db_one("SELECT COUNT(*) as c FROM cities WHERE status='active'") or {"c":0}
    cities         = cities_count["c"]
    infra_score    = min(100, (cities * 20) + (total_alliances * 15) +
                       (sum(1 for t in territory_states.values() if t["state"]=="liberated") * 10) +
                       (active_laws * 5))

    # ── Chapter Info ──
    chapter = db_one("SELECT * FROM chapters ORDER BY id DESC LIMIT 1")
    if not chapter:
        chapter = {"id": 1, "name": "Chapter 1", "status": "active", "boss_id": None}

    # ── Dark Age ──
    dark_age = infra_score < 30 and not boss_alive

    # ── Day/Night ──
    # 18h day / 6h night cycle based on real UTC time
    hour = datetime.datetime.utcnow().hour
    is_night = hour >= 20 or hour < 2  # 20:00 - 02:00 UTC = night

    return {
        "chapter":         chapter,
        "world": {
            "era":            "The Age of Emergence",
            "is_dark_age":    dark_age,
            "is_night":       is_night,
            "infrastructure_score": infra_score,
        },
        "stats": {
            "total_characters": total_chars,
            "nft_characters":   nft_chars,
            "total_legacy":     total_legacy,
            "total_strength":   total_strength,
            "monsters_alive":   len(monsters_alive),
            "monsters_slain":   len(monsters_slain),
            "active_alliances": total_alliances,
            "active_laws":      active_laws,
            "boss_alive":       boss_alive,
        },
        "territories":     territory_states,
    }

# ──────────────────────────────────────────────────────────────
# ROUTES
# ──────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "Nixon RPG World API online", "version": "1.0.0"}

@app.get("/world")
def get_world():
    """Full world state — used by Phaser.js to render the map."""
    return calculate_world_state()

@app.get("/characters")
def get_characters(limit: int = 20):
    """All active characters with stats."""
    chars = db_query("SELECT * FROM characters WHERE hp > 0 LIMIT ?", (limit,))
    result = []
    for c in chars:
        stats    = json.loads(c.get("stats","{}"))
        identity = json.loads(c.get("identity","{}"))
        result.append({
            "id":        c["user_id"],
            "name":      c["name"],
            "is_nft":    bool(c.get("is_nft")),
            "archetype": identity.get("archetype","wanderer"),
            "territory": identity.get("territory","Free Haven"),
            "stats":     stats,
            "hp":        c.get("hp",100),
            "created_at":c.get("created_at",""),
        })
    return result

@app.get("/characters/{user_id}")
def get_character(user_id: str):
    """Single character by Discord user ID."""
    c = db_one("SELECT * FROM characters WHERE user_id=?", (user_id,))
    if not c:
        raise HTTPException(status_code=404, detail="Character not found")
    return {
        "id":        c["user_id"],
        "name":      c["name"],
        "is_nft":    bool(c.get("is_nft")),
        "identity":  json.loads(c.get("identity","{}")),
        "stats":     json.loads(c.get("stats","{}")),
        "memory":    json.loads(c.get("memory","[]"))[-10:],
        "hp":        c.get("hp",100),
    }

@app.get("/monsters")
def get_monsters(status: str = "alive"):
    """All monsters — alive or dead."""
    monsters = db_query("SELECT * FROM monsters WHERE status=?", (status,))
    return [{
        "id":        m["id"],
        "name":      m["name"],
        "tier":      m["tier"],
        "is_boss":   bool(m.get("is_boss")),
        "territory": m.get("territory",""),
        "hp":        m.get("hp",100),
        "max_hp":    m.get("max_hp",100),
        "stats":     json.loads(m.get("stats","{}")),
        "status":    m.get("status","alive"),
        "slain_by":  m.get("slain_by_name",""),
    } for m in monsters]

@app.get("/territories")
def get_territories():
    """Territory states with full details."""
    state = calculate_world_state()
    return state["territories"]

@app.get("/alliances")
def get_alliances():
    """All active alliances."""
    alliances = db_query("SELECT * FROM alliances WHERE disbanded_at IS NULL")
    return [{
        "id":      a["id"],
        "name":    a.get("name",""),
        "members": json.loads(a.get("members","[]")),
        "oath":    a.get("oath",""),
        "formed":  a.get("formed_at",""),
    } for a in alliances]

@app.get("/laws")
def get_laws():
    """All passed laws currently in effect."""
    laws = db_query(
        "SELECT * FROM world_events WHERE type='law' AND status='passed' ORDER BY created_at DESC"
    )
    return [{
        "id":          l["id"],
        "title":       l.get("title",""),
        "description": l.get("description",""),
        "created_at":  l.get("created_at",""),
    } for l in laws]

@app.get("/chapters")
def get_chapters():
    """All chapters with history."""
    chapters = db_query("SELECT * FROM chapters ORDER BY id DESC")
    if not chapters:
        return [{"id":1,"name":"Chapter 1","status":"active"}]
    return chapters

@app.get("/chapters/current")
def get_current_chapter():
    """Current active chapter."""
    chapter = db_one("SELECT * FROM chapters WHERE status='active' ORDER BY id DESC LIMIT 1")
    if not chapter:
        return {"id":1,"name":"Chapter 1","status":"active","boss":None}

    boss = None
    if chapter.get("boss_id"):
        boss = db_one("SELECT * FROM monsters WHERE id=?", (chapter["boss_id"],))

    return {
        "chapter": chapter,
        "boss":    boss,
        "infra_score": calculate_world_state()["world"]["infrastructure_score"],
    }

@app.get("/leaderboard")
def get_leaderboard():
    """Top players by legacy."""
    chars = db_query("SELECT * FROM characters WHERE hp > 0")
    ranked = []
    for c in chars:
        stats = json.loads(c.get("stats","{}"))
        ranked.append({
            "name":     c["name"],
            "is_nft":   bool(c.get("is_nft")),
            "legacy":   stats.get("legacy",0),
            "strength": stats.get("strength",0),
            "wisdom":   stats.get("wisdom",0),
        })
    ranked.sort(key=lambda x: x["legacy"], reverse=True)
    return ranked[:20]

@app.get("/heroes")
def get_hall_of_heroes():
    """Hall of Heroes — characters who slew bosses."""
    heroes = db_query(
        "SELECT * FROM monsters WHERE is_boss=1 AND status='dead' ORDER BY slain_at DESC"
    )
    return [{
        "boss_name":  h["name"],
        "slain_by":   h.get("slain_by_name","Unknown"),
        "territory":  h.get("territory",""),
        "slain_at":   h.get("slain_at",""),
    } for h in heroes]

@app.get("/pvp")
def get_pvp_rankings():
    """PvP rankings."""
    rows = db_query(
        "SELECT * FROM pvp_stats ORDER BY wins DESC, streak DESC LIMIT 20"
    )
    return rows

@app.get("/world/history")
def get_world_history():
    """World event history."""
    events = db_query(
        "SELECT * FROM world_events ORDER BY created_at DESC LIMIT 50"
    )
    return events

@app.get("/infrastructure")
def get_infrastructure():
    """Infrastructure score and breakdown."""
    state = calculate_world_state()
    cities = db_query("SELECT * FROM cities WHERE status='active'") if table_exists("cities") else []
    return {
        "score":      state["world"]["infrastructure_score"],
        "is_dark_age":state["world"]["is_dark_age"],
        "cities":     len(cities),
        "alliances":  state["stats"]["active_alliances"],
        "laws":       state["stats"]["active_laws"],
        "liberated_territories": sum(
            1 for t in state["territories"].values() if t["state"]=="liberated"
        ),
    }

def table_exists(name: str) -> bool:
    result = db_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return result is not None

# ──────────────────────────────────────────────────────────────
# RUN
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print(f"⚔  Nixon RPG World API starting on port {API_PORT}")
    print(f"📖 DB: {DB_PATH}")
    print(f"🌐 Docs: http://localhost:{API_PORT}/docs")
    uvicorn.run("api_server:app", host="0.0.0.0", port=API_PORT, reload=False)
