"""
modules/world_map.py
────────────────────────────────────────────────────────────────
Dynamic World Map System — Nixon RPG Bot

World map is generated as PNG and updated after each chapter based on
real world events (territory liberation, events, character actions).

Commands:
  !map              → current world map as PNG
  !map history      → chapter history
  !mapinfo [name]   → detailed info about a territory
  !chapter end      → (Admin) close chapter + AI updates the map

Install:
  pip install Pillow numpy
"""

import os, io, json, sqlite3, asyncio, aiohttp, datetime, hashlib, math, random, re
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np
import discord
from discord.ext import commands

# ── Config ────────────────────────────────────────────────────
AIMLAPI_KEY = os.getenv("AIMLAPI_KEY")
AIMLAPI_URL = "https://api.aimlapi.com/v1/chat/completions"
GM_MODEL    = "x-ai/grok-4-1-fast-non-reasoning"
DB_PATH     = "rpg.db"
CANVAS_W, CANVAS_H = 900, 620

# ── Biome palettes ───────────────────────────────────────────
BIOME_PALETTES = {
    "void":      {"deep": (18,8,35),    "mid": (45,20,80),   "light": (90,50,140),  "accent": (140,80,200)},
    "safe":      {"deep": (5,30,12),    "mid": (15,65,30),   "light": (30,110,55),  "accent": (80,180,100)},
    "fire":      {"deep": (40,8,5),     "mid": (100,30,10),  "light": (180,70,20),  "accent": (240,120,40)},
    "cursed":    {"deep": (5,20,10),    "mid": (12,45,22),   "light": (25,80,40),   "accent": (50,140,70)},
    "undead":    {"deep": (10,10,25),   "mid": (25,25,55),   "light": (50,50,100),  "accent": (100,100,180)},
    "ruins":     {"deep": (30,22,10),   "mid": (70,52,22),   "light": (120,90,40),  "accent": (180,145,75)},
    "ice":       {"deep": (8,20,35),    "mid": (20,55,90),   "light": (50,120,170), "accent": (130,200,240)},
    "storm":     {"deep": (20,18,40),   "mid": (45,42,90),   "light": (80,75,160),  "accent": (130,125,220)},
    "liberated": {"deep": (8,35,15),    "mid": (20,80,38),   "light": (40,140,70),  "accent": (100,220,130)},
    "fallen":    {"deep": (40,5,5),     "mid": (90,15,15),   "light": (160,35,35),  "accent": (220,70,70)},
    "default":   {"deep": (20,18,30),   "mid": (45,40,65),   "light": (80,72,110),  "accent": (130,120,170)},
}

# ── Initial world map — polygon-based ───────────────────────
BASE_MAP_LAYOUT = {
    "The Void": {
        "poly": [(60,160),(180,140),(220,200),(240,300),(200,370),(100,360),(55,280)],
        "biome": "void", "label_pos": (140, 258),
    },
    "Free Haven": {
        "poly": [(220,200),(360,185),(390,250),(370,340),(330,390),(240,380),(200,310)],
        "biome": "safe", "label_pos": (292, 288),
    },
    "The Forge": {
        "poly": [(370,60),(530,50),(570,130),(555,220),(450,240),(360,185),(340,110)],
        "biome": "fire", "label_pos": (460, 148),
    },
    "Darkwood": {
        "poly": [(370,340),(450,330),(560,360),(575,460),(490,510),(360,490),(330,410)],
        "biome": "cursed", "label_pos": (455, 422),
    },
    "The Sunken Keep": {
        "poly": [(555,180),(700,160),(760,250),(750,380),(680,430),(575,410),(550,310),(555,220)],
        "biome": "undead", "label_pos": (652, 298),
    },
}

BASE_CONNECTIONS = [
    ("The Void", "Free Haven"),
    ("Free Haven", "The Forge"),
    ("Free Haven", "Darkwood"),
    ("The Forge", "The Sunken Keep"),
    ("Darkwood", "The Sunken Keep"),
]

# ── Pre-defined positions for new territories ────────────────
NEW_TERRITORY_HINTS = {
    "top-left":      [(80,70),(200,55),(230,130),(210,200),(110,210)],
    "top-right":     [(760,60),(870,70),(880,160),(820,200),(720,170)],
    "bottom-left":   [(50,450),(180,440),(210,530),(170,580),(60,570)],
    "bottom-right":  [(720,470),(840,460),(870,560),(820,590),(700,580)],
    "top-center":    [(360,55),(490,45),(530,120),(510,185),(370,175)],
    "bottom-center": [(340,510),(480,510),(510,585),(480,610),(340,610)],
}


# ── DB Helpers ────────────────────────────────────────────────

def _get_db():
    return sqlite3.connect(DB_PATH)

def init_map_db():
    con = _get_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS map_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter      INTEGER NOT NULL,
            chapter_name TEXT,
            map_layout   TEXT NOT NULL,
            world_snap   TEXT NOT NULL,
            changes      TEXT,
            connections  TEXT,
            snap_hash    TEXT,
            created_at   TEXT NOT NULL
        )
    """)
    con.commit(); con.close()

def _load_world() -> dict:
    con = _get_db()
    row = con.execute("SELECT state FROM world_state WHERE id=1").fetchone()
    con.close()
    return json.loads(row[0]) if row else {}

def _save_world(state: dict):
    con = _get_db()
    con.execute("UPDATE world_state SET state=?,updated_at=? WHERE id=1",
                (json.dumps(state), datetime.datetime.utcnow().isoformat()))
    con.commit(); con.close()

def _get_current_chapter() -> int:
    con = _get_db()
    row = con.execute("SELECT MAX(chapter) FROM map_history").fetchone()
    con.close()
    return row[0] or 0

def _load_latest_layout():
    """Returns (layout_dict, connections_list) — falls back to base if not found."""
    con = _get_db()
    row = con.execute(
        "SELECT map_layout, connections FROM map_history ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    if row:
        layout = json.loads(row[0])
        for info in layout.values():
            info["poly"]      = [tuple(p) for p in info["poly"]]
            info["label_pos"] = tuple(info["label_pos"])
        conns = json.loads(row[1]) if row[1] else BASE_CONNECTIONS
        conns = [tuple(c) for c in conns]
        return layout, conns
    return {k: dict(v) for k, v in BASE_MAP_LAYOUT.items()}, list(BASE_CONNECTIONS)

def _save_map_snapshot(chapter, chapter_name, layout, world, changes, connections):
    payload = {"ch": chapter, "layout": {k: v.get("biome") for k,v in layout.items()}}
    h = "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    serial = {}
    for name, info in layout.items():
        serial[name] = {**info,
                        "poly":      [list(p) for p in info["poly"]],
                        "label_pos": list(info["label_pos"])}
    con = _get_db()
    con.execute(
        "INSERT INTO map_history"
        " (chapter,chapter_name,map_layout,world_snap,changes,connections,snap_hash,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (chapter, chapter_name, json.dumps(serial), json.dumps(world),
         json.dumps(changes), json.dumps([list(c) for c in connections]),
         h, datetime.datetime.utcnow().isoformat())
    )
    con.commit(); con.close()
    return h


# ── Font helper ───────────────────────────────────────────────

def _font(size, bold=False):
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for p in candidates:
        try: return ImageFont.truetype(p, size)
        except Exception: continue
    return ImageFont.load_default()


# ── Drawing Helpers ───────────────────────────────────────────

def _starfield(img):
    rng = random.Random(42)
    arr = np.array(img)
    for _ in range(150):
        x = rng.randint(0, CANVAS_W-1)
        y = rng.randint(52, CANVAS_H-1)
        b = rng.randint(25, 85)
        arr[y, x] = [b, b, b+15]
    return Image.fromarray(arr)

def _fill_territory(img, poly, palette, seed):
    rng = random.Random(seed)
    layer = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    ld    = ImageDraw.Draw(layer)
    ld.polygon(poly, fill=(*palette["deep"], 255))
    xs=[p[0] for p in poly]; ys=[p[1] for p in poly]
    x0,y0,x1,y1 = min(xs),min(ys),max(xs),max(ys)
    for _ in range(20):
        rx=rng.randint(x0,x1); ry=rng.randint(y0,y1)
        r=rng.randint(20,55);   a=rng.randint(50,110)
        ld.ellipse([rx-r,ry-r,rx+r,ry+r], fill=(*palette["mid"], a))
    cx=(x0+x1)//2; cy=(y0+y1)//2
    cr=int(math.dist((x0,y0),(x1,y1))*0.18)
    ld.ellipse([cx-cr,cy-cr,cx+cr,cy+cr], fill=(*palette["light"], 60))
    base = img.convert("RGBA")
    base.paste(layer, mask=layer)
    img.paste(base.convert("RGB"))

def _glow_border(img, poly, color, width=4):
    glow = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    gd   = ImageDraw.Draw(glow)
    pts  = poly + [poly[0]]
    for w in range(width, 0, -1):
        a = int(200*w/width)
        for j in range(len(pts)-1):
            gd.line([pts[j],pts[j+1]], fill=(*color,a), width=w*3)
    glow = glow.filter(ImageFilter.GaussianBlur(3))
    base = img.convert("RGBA")
    img.paste(Image.alpha_composite(base, glow).convert("RGB"))

def _draw_roads(draw, layout, connections):
    for a, b in connections:
        if a not in layout or b not in layout: continue
        ax,ay=layout[a]["label_pos"]; bx,by=layout[b]["label_pos"]
        steps=max(10,int(math.dist((ax,ay),(bx,by))/15))
        for i in range(0,steps,2):
            sx=int(ax+(bx-ax)*i/steps);       sy=int(ay+(by-ay)*i/steps)
            ex=int(ax+(bx-ax)*min(i+1,steps)/steps); ey=int(ay+(by-ay)*min(i+1,steps)/steps)
            draw.line([(sx,sy),(ex,ey)], fill=(50,42,68), width=1)


# ── Main Map Generator ────────────────────────────────────────

def generate_map_image(layout: dict, world: dict, connections: list,
                       chapter: int, chapter_name: str = "") -> io.BytesIO:
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), (5,3,10))
    img = _starfield(img)

    # grid
    grid = Image.new("RGBA",(CANVAS_W,CANVAS_H),(0,0,0,0))
    gd   = ImageDraw.Draw(grid)
    for x in range(0,CANVAS_W,50): gd.line([(x,52),(x,CANVAS_H)],fill=(18,14,30,55))
    for y in range(52,CANVAS_H,50): gd.line([(0,y),(CANVAS_W,y)],fill=(18,14,30,55))
    img = Image.alpha_composite(img.convert("RGBA"),grid).convert("RGB")

    draw = ImageDraw.Draw(img,"RGBA")
    _draw_roads(draw, layout, connections)

    territories = world.get("territories", {})

    for name, info in layout.items():
        poly   = info["poly"]
        biome  = info.get("biome","default")
        palette= BIOME_PALETTES.get(biome, BIOME_PALETTES["default"])
        wt     = territories.get(name, {})
        mon    = wt.get("monster_controlled", info.get("monster_controlled", False))
        ctrl   = wt.get("controller", info.get("controller"))
        info["monster_controlled"] = mon
        info["controller"]         = ctrl

        ep = BIOME_PALETTES["liberated"] if (ctrl and not mon) else palette
        bc = (80,220,110) if (ctrl and not mon) else ((200,50,50) if mon else palette["accent"])
        if info.get("is_new"): bc = (255,215,0)

        _fill_territory(img, poly, ep, seed=hash(name)%9999)
        _glow_border(img, poly, bc, width=4)

        d2 = ImageDraw.Draw(img)
        pts = poly+[poly[0]]
        for j in range(len(pts)-1):
            d2.line([pts[j],pts[j+1]], fill=bc, width=2)

    d3 = ImageDraw.Draw(img)
    for name, info in layout.items():
        lx,ly  = info["label_pos"]
        mon    = info.get("monster_controlled",False)
        ctrl   = info.get("controller")
        is_new = info.get("is_new",False)
        pal    = BIOME_PALETTES.get(info.get("biome","default"), BIOME_PALETTES["default"])

        d3.text((lx+1,ly+1), name, font=_font(13,bold=True), fill=(0,0,0), anchor="mm")
        d3.text((lx,  ly  ), name, font=_font(13,bold=True), fill=pal["accent"], anchor="mm")

        if mon:    st,sc = "☠ Monster Zone",(220,80,80)
        elif ctrl: st,sc = f"► {ctrl[:16]}",(100,220,130)
        else:      st,sc = "◌ Unclaimed",(140,132,155)
        d3.text((lx+1,ly+16), st, font=_font(10), fill=(0,0,0), anchor="mm")
        d3.text((lx,  ly+15), st, font=_font(10), fill=sc,      anchor="mm")

        if is_new:
            bx,by2 = lx+38,ly-23
            d3.rectangle([bx-22,by2-8,bx+22,by2+8], fill=(160,120,0), outline=(255,215,0))
            d3.text((bx,by2),"✦ NEW", font=_font(9,bold=True), fill=(255,245,180), anchor="mm")

    # Header
    hdr = Image.new("RGBA",(CANVAS_W,52),(6,3,14,230))
    hd  = ImageDraw.Draw(hdr)
    for x in range(CANVAS_W):
        r=int(50+30*math.sin(x/CANVAS_W*math.pi))
        hd.line([(x,50),(x,52)],fill=(r,25,70,200))
    img.paste(hdr.convert("RGB"),(0,0))

    dh = ImageDraw.Draw(img)
    era=world.get("era","The Age of Emergence")
    year=world.get("year",1); day=world.get("day",1)
    ch_str=f"Chapter {chapter}"+(f": {chapter_name}" if chapter_name else "")
    dh.text((14,8),  f"  {era}", font=_font(15,bold=True), fill=(215,195,255))
    dh.text((14,30), f"Year {year}  •  Day {day}  •  {ch_str}", font=_font(10), fill=(145,125,175))

    # power bar
    total=len(layout)
    lib  =sum(1 for i in layout.values() if i.get("controller") and not i.get("monster_controlled"))
    mon_c=sum(1 for i in layout.values() if i.get("monster_controlled"))
    ratio=lib/max(total,1)
    bx,by,bw,bh=CANVAS_W-225,12,205,11
    dh.rectangle([bx,by,bx+bw,by+bh],fill=(25,12,40),outline=(70,45,100))
    if ratio>0: dh.rectangle([bx,by,bx+int(bw*ratio),by+bh],fill=(55,175,85))
    dh.text((bx,by+15),f"Liberated {lib}/{total}   ☠ {mon_c} monster zones",font=_font(10),fill=(155,140,175))

    # legend
    lx,ly=8,CANVAS_H-72
    dh.rectangle([lx-2,ly-4,lx+315,ly+68],fill=(6,4,14),outline=(35,28,55))
    dh.text((lx+4,ly),"LEGEND",font=_font(9,bold=True),fill=(155,135,185))
    for i,(col,label) in enumerate([
        ((80,220,110),"Liberated"),((200,50,50),"Monster Zone"),
        ((255,215,0),"New Region"),((140,132,155),"Unclaimed"),((50,42,68),"Travel Route")
    ]):
        ci,ri=i%2,i//2
        ox=lx+4+ci*158; oy=ly+14+ri*17
        dh.rectangle([ox,oy+2,ox+11,oy+11],fill=col)
        dh.text((ox+15,oy),label,font=_font(10),fill=(175,165,195))

    # compass
    cx2,cy2,cr2=CANVAS_W-42,CANVAS_H-42,26
    dh.ellipse([cx2-cr2,cy2-cr2,cx2+cr2,cy2+cr2],outline=(65,50,95),width=1)
    for angle,label,col in[(270,"N",(215,195,255)),(90,"S",(170,155,200)),
                             (0,"E",(170,155,200)),(180,"W",(170,155,200))]:
        rad=math.radians(angle)
        tx=cx2+int((cr2-8)*math.cos(rad)); ty=cy2+int((cr2-8)*math.sin(rad))
        dh.text((tx,ty),label,font=_font(9,bold=True),fill=col,anchor="mm")
    dh.line([(cx2,cy2),(cx2,cy2-cr2+4)],fill=(215,195,255),width=2)
    dh.polygon([(cx2,cy2-cr2+2),(cx2-3,cy2-cr2+10),(cx2+3,cy2-cr2+10)],fill=(215,195,255))

    buf=io.BytesIO(); img.save(buf,format="PNG",optimize=True); buf.seek(0)
    return buf


# ── AI Map Update ─────────────────────────────────────────────

async def _ai_update_map(world, layout, connections, chapter, events):
    events_text="\n".join(f"- {e.get('event','')} [actor: {e.get('actor','?')}]" for e in events[-25:]) or "No major events."
    terr_json=json.dumps(world.get("territories",{}),ensure_ascii=False,indent=2)
    layout_min={k:{"biome":v.get("biome"),"monster":v.get("monster_controlled"),"ctrl":v.get("controller")} for k,v in layout.items()}
    conns_text=", ".join(f"{a}↔{b}" for a,b in connections)

    prompt=f"""You are the cartographer of a living RPG world. Chapter {chapter} just ended.

WORLD: Era={world.get('era')} Year={world.get('year')} Day={world.get('day')} Power={world.get('power_balance')}
TERRITORIES: {terr_json}
MAP BIOMES: {json.dumps(layout_min,ensure_ascii=False)}
CONNECTIONS: {conns_text}
CHAPTER EVENTS:
{events_text}

Output ONLY valid JSON:
{{
  "chapter_name": "3-5 word dramatic name",
  "changes": ["brief change description (max 6)"],
  "territory_biome_changes": {{"ExistingName": "new_biome"}},
  "new_territories": [{{"name":"Name","biome":"void|safe|fire|cursed|undead|ruins|ice|storm","description":"one sentence","connects_to":["ExistingName"],"poly_hint":"top-left|top-right|bottom-left|bottom-right|top-center|bottom-center"}}],
  "removed_territories": ["name (never Free Haven or The Void)"],
  "new_connections": [["A","B"]],
  "removed_connections": [["A","B"]],
  "world_lore_addition": "one sentence"
}}
Biome options: void safe fire cursed undead ruins ice storm liberated fallen
Only change biomes/territories if events strongly justify it."""

    headers={"Authorization":f"Bearer {AIMLAPI_KEY}","Content-Type":"application/json"}
    body={"model":GM_MODEL,"max_tokens":2000,"messages":[{"role":"user","content":prompt}]}
    async with aiohttp.ClientSession() as s:
        async with s.post(AIMLAPI_URL,headers=headers,json=body) as r:
            data=await r.json()
    raw=data["choices"][0]["message"]["content"].strip()
    clean=raw.lstrip("```json").lstrip("```").rstrip("```").strip()
    try: return json.loads(clean)
    except Exception:
        m=re.search(r'\{[\s\S]*\}',clean)
        return json.loads(m.group()) if m else {}


def _apply_ai_changes(layout, connections, world, ai):
    new_layout={k:dict(v) for k,v in layout.items()}
    new_conns=list(connections)
    for info in new_layout.values(): info["is_new"]=False

    for name,biome in ai.get("territory_biome_changes",{}).items():
        if name in new_layout: new_layout[name]["biome"]=biome

    safe={"Free Haven","The Void"}
    for removed in ai.get("removed_territories",[]):
        if removed in new_layout and removed not in safe:
            del new_layout[removed]
            world.get("territories",{}).pop(removed,None)
            new_conns=[(a,b) for a,b in new_conns if a!=removed and b!=removed]

    for nt in ai.get("new_territories",[]):
        name=nt.get("name")
        if not name or name in new_layout: continue
        hint=nt.get("poly_hint","top-right")
        poly=[tuple(p) for p in NEW_TERRITORY_HINTS.get(hint,NEW_TERRITORY_HINTS["top-right"])]
        xs=[p[0] for p in poly]; ys=[p[1] for p in poly]
        new_layout[name]={"poly":poly,"biome":nt.get("biome","default"),
                          "label_pos":(sum(xs)//len(xs),sum(ys)//len(ys)),
                          "is_new":True,"monster_controlled":nt.get("biome") in ("fire","cursed","undead","fallen"),
                          "controller":None}
        world.setdefault("territories",{})[name]={
            "controller":None,
            "monster_controlled":nt.get("biome") in ("fire","cursed","undead","fallen"),
            "description":nt.get("description","A newly discovered region.")}
        for ct in nt.get("connects_to",[]):
            if ct in new_layout: new_conns.append((name,ct))

    for pair in ai.get("new_connections",[]):
        if len(pair)==2 and pair[0] in new_layout and pair[1] in new_layout:
            new_conns.append(tuple(pair))
    for pair in ai.get("removed_connections",[]):
        if len(pair)==2:
            new_conns=[(a,b) for a,b in new_conns if not {a,b}=={pair[0],pair[1]}]

    seen=set(); deduped=[]
    for a,b in new_conns:
        k=frozenset([a,b])
        if k not in seen: seen.add(k); deduped.append((a,b))
    return new_layout, deduped


# ── Discord Setup ─────────────────────────────────────────────

def setup(bot):
    init_map_db()

    @bot.command(name="map")
    async def show_map(ctx, sub: str = None):
        """!map — current map  |  !map history — chapter history"""
        if sub == "history":
            con=_get_db()
            rows=con.execute("SELECT chapter,chapter_name,changes,created_at FROM map_history ORDER BY id DESC LIMIT 8").fetchall()
            con.close()
            if not rows:
                await ctx.reply("📜 No chapter history yet. Use `!chapter end` to close a chapter.")
                return
            embed=discord.Embed(title="📚 World Chronicle — Map History",
                                description="How the world has changed through the ages.",color=0x3a1060)
            for ch,ch_name,cj,ts in rows:
                changes=json.loads(cj) if cj else []
                text="\n".join(f"• {c}" for c in changes[:5]) or "_No major changes._"
                embed.add_field(name=f"📖 Ch {ch}: {ch_name or 'Unnamed'}  ·  {ts[:10]}",value=text,inline=False)
            await ctx.reply(embed=embed)
            return

        async with ctx.typing():
            world=_load_world(); layout,conns=_load_latest_layout()
            chapter=_get_current_chapter()
            buf=generate_map_image(layout,world,conns,chapter)
            file=discord.File(buf,filename="world_map.png")

        territories=world.get("territories",{})
        total=len(territories)
        lib =sum(1 for t in territories.values() if t.get("controller") and not t.get("monster_controlled"))
        mon =sum(1 for t in territories.values() if t.get("monster_controlled"))
        embed=discord.Embed(
            title=f"🗺️  World Map  —  Chapter {chapter}",
            description=(f"**{world.get('era','?')}**  •  Year **{world.get('year',1)}**, Day **{world.get('day',1)}**\n\n"
                         f"🟢 Liberated: **{lib}**  ·  🔴 Monster Zones: **{mon}**  ·  ⚪ Unclaimed: **{total-lib-mon}**"),
            color=0x1a0a30)
        embed.set_image(url="attachment://world_map.png")
        embed.set_footer(text="!chapter end — close chapter & evolve the map  |  !mapinfo — territory details")
        await ctx.reply(embed=embed,file=file)

    @bot.command(name="chapter")
    @commands.has_permissions(administrator=True)
    async def chapter_cmd(ctx, action: str = None):
        """!chapter end — closes chapter and updates map with AI (Admin)"""
        if action != "end":
            await ctx.reply("**Chapter Commands:**\n`!chapter end` — Close chapter & evolve the world map *(Admin)*")
            return

        current=_get_current_chapter(); next_ch=current+1
        msg=await ctx.reply(f"⌛ **Closing Chapter {current}...**\nThe cartographer reads the winds of change...")

        async with ctx.typing():
            world=_load_world(); layout,conns=_load_latest_layout()
            events=world.get("notable_events",[])
            try:
                ai=await _ai_update_map(world,layout,conns,current,events)
            except Exception as e:
                await msg.edit(content=f"❌ AI error: {e}"); return

            chapter_name=ai.get("chapter_name",f"Chapter {current}")
            changes=ai.get("changes",[])
            lore_add=ai.get("world_lore_addition","")
            new_layout,new_conns=_apply_ai_changes(layout,conns,world,ai)

            if lore_add: world["world_lore"]=world.get("world_lore","")+(" "+lore_add)
            world["notable_events"]=[]
            _save_world(world)
            snap_hash=_save_map_snapshot(next_ch,chapter_name,new_layout,world,changes,new_conns)
            buf=generate_map_image(new_layout,world,new_conns,next_ch,chapter_name)
            file=discord.File(buf,filename=f"chapter_{next_ch}_map.png")

        changes_text="\n".join(f"• {c}" for c in changes) or "_The world endures, unchanged._"
        embed=discord.Embed(
            title=f"📖 Chapter {current} Ends — *{chapter_name}*",
            description=(f"The world has been reshaped by blood, valor, and consequence.\n**Chapter {next_ch}** dawns.\n\n"
                         f"**🗺️ Map Changes:**\n{changes_text}"),
            color=0xffd700)
        if lore_add: embed.add_field(name="📜 New Lore",value=lore_add,inline=False)
        embed.add_field(name="🔑 Snapshot Hash",value=f"`{snap_hash[:60]}...`",inline=False)
        embed.set_image(url=f"attachment://chapter_{next_ch}_map.png")
        embed.set_footer(text=f"Chapter {next_ch} has begun. The world watches.")
        await msg.delete()
        await ctx.send(content=f"@here  📖 **Chapter {current}: *{chapter_name}* — has ended!**",embed=embed,file=file)

    @bot.command(name="mapinfo")
    async def map_info(ctx, *, territory: str = None):
        """!mapinfo [territory] — full info about a territory"""
        world=_load_world(); layout,_=_load_latest_layout()
        territories=world.get("territories",{})
        if not territory:
            lines=[]
            for name,info in layout.items():
                wt=territories.get(name,{}); ctrl=wt.get("controller") or info.get("controller")
                mon=wt.get("monster_controlled",info.get("monster_controlled",False))
                icon="☠️" if mon else ("✅" if ctrl else "◌")
                lines.append(f"{icon} **{name}** `[{info.get('biome','?')}]`"+(f" — {ctrl}" if ctrl else ""))
            embed=discord.Embed(title="🗺️ Territory Overview",description="\n".join(lines) or "No territories.",color=0x1a0a30)
            embed.set_footer(text="!mapinfo <name> for details  |  !map for visual")
            await ctx.reply(embed=embed); return

        matched=next((n for n in layout if n.lower()==territory.lower()),None)
        if not matched: matched=next((n for n in layout if territory.lower() in n.lower()),None)
        if not matched:
            await ctx.reply(f"Territory `{territory}` not found. Try `!mapinfo` for the list."); return

        wt=territories.get(matched,{}); li=layout[matched]
        mon=wt.get("monster_controlled",li.get("monster_controlled",False))
        ctrl=wt.get("controller",li.get("controller"))
        embed=discord.Embed(title=f"{'☠️' if mon else '🏔️'} {matched}",
                            description=wt.get("description","No description."),
                            color=0xcc2222 if mon else (0x22cc55 if ctrl else 0x444455))
        embed.add_field(name="Biome",value=li.get("biome","?").title(),inline=True)
        embed.add_field(name="Controller",value=ctrl or "Unclaimed",inline=True)
        embed.add_field(name="Status",value="☠️ Monster Zone" if mon else ("✅ Liberated" if ctrl else "◌ Free"),inline=True)
        await ctx.reply(embed=embed)
