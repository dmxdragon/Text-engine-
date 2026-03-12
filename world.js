/**
 * Nixon RPG — World Scene
 * Main game world — tiles, characters, monsters, UI
 */

class WorldScene extends Scene {
  constructor(engine, data = {}) {
    super(engine, data);
    this.tilemap    = null;
    this.characters = [];
    this.monsters   = [];
    this._uiData    = {};
    this._infoPanel = null;
    this._selected  = null;
    this._nightAlpha= 0;
    this._dayTimer  = 0;
  }

  async preload() {
    // Queue assets (placeholders used if files don't exist yet)
    this.engine.loader
      .tileset("tiles",         "assets/tiles/tileset.png",     32, 32)
      .spriteSheet("hero_warrior",  "assets/sprites/hero_warrior.png",  32, 48, {
        idle:   { row:0, frames:4, fps:6 },
        walk:   { row:1, frames:6, fps:10 },
        attack: { row:2, frames:4, fps:12, loop:false },
        hurt:   { row:3, frames:2, fps:8,  loop:false },
        death:  { row:4, frames:5, fps:6,  loop:false },
      })
      .spriteSheet("hero_archer", "assets/sprites/hero_archer.png", 32, 48, {
        idle:   { row:0, frames:4, fps:6 },
        walk:   { row:1, frames:6, fps:10 },
        attack: { row:2, frames:4, fps:12, loop:false },
      })
      .spriteSheet("hero_mage", "assets/sprites/hero_mage.png", 32, 48, {
        idle:   { row:0, frames:4, fps:6 },
        walk:   { row:1, frames:6, fps:10 },
        attack: { row:2, frames:4, fps:12, loop:false },
      })
      .spriteSheet("monster_common", "assets/sprites/monster_common.png", 32, 32, {
        idle:   { row:0, frames:4, fps:5 },
        attack: { row:1, frames:3, fps:10, loop:false },
        death:  { row:2, frames:4, fps:6,  loop:false },
      })
      .spriteSheet("monster_boss", "assets/sprites/monster_boss.png", 48, 48, {
        idle:   { row:0, frames:4, fps:4 },
        attack: { row:1, frames:5, fps:8, loop:false },
        death:  { row:2, frames:6, fps:5, loop:false },
      });

    await this.engine.loader.loadAll((progress, name) => {
      this.engine.emit("loading:progress", { progress, name });
    });
  }

  async create() {
    // Build tilemap
    this.tilemap = new TileMap(this.engine, { width: 80, height: 60, tileSize: 32 });
    this.tilemap.setTileset("tiles");

    // Setup camera bounds
    this.engine.camera.setBounds(0, 0, this.tilemap.pixelWidth, this.tilemap.pixelHeight);
    this.engine.camera.panTo(
      this.tilemap.pixelWidth  / 2 - this.engine.config.width  / (2 * this.engine.camera.zoom),
      this.tilemap.pixelHeight / 2 - this.engine.config.height / (2 * this.engine.camera.zoom),
      true
    );

    // Input: click on world
    this.engine.on("input:mousedown", (e) => this._onWorldClick(e));

    // API update handler
    this.engine.on("api:updated", (data) => this._onAPIUpdate(data));

    // Fetch initial data
    await this.engine.api.fetchAll();

    console.log("[WorldScene] Created");
  }

  _onAPIUpdate(data) {
    const { world, chars, monsters } = data;
    this._uiData = world;

    // Update tilemap territories
    if (world?.territories) {
      this.tilemap.generateFromWorldData(world);
    }

    // Rebuild characters
    this.characters.forEach(c => { c.destroy(); this.remove(c); });
    this.characters = [];
    chars.forEach((charData, i) => {
      const zone   = this._getTerritoryCenter(charData.territory || "Free Haven");
      const offset = { x: (Math.sin(i * 1.3) * 60), y: (Math.cos(i * 0.9) * 40) };
      const char   = new CharacterEntity(this.engine, zone.x + offset.x, zone.y + offset.y, charData);
      this.characters.push(char);
      this.add(char);
    });

    // Rebuild monsters
    this.monsters.forEach(m => { m.destroy(); this.remove(m); });
    this.monsters = [];
    monsters.forEach((monData, i) => {
      const zone   = this._getTerritoryCenter(monData.territory || "The Void");
      const offset = { x: (Math.cos(i * 2.1) * 70), y: (Math.sin(i * 1.7) * 50) };
      const mon    = new MonsterEntity(this.engine, zone.x + offset.x, zone.y + offset.y, monData);
      this.monsters.push(mon);
      this.add(mon);
    });

    // Chapter transition check
    if (world?.chapter && this._lastChapterId && world.chapter.id !== this._lastChapterId) {
      this.engine.scenes.chapterTransition(world);
    }
    this._lastChapterId = world?.chapter?.id;

    // Dark age / night
    this._isNight    = world?.world?.is_night    || false;
    this._isDarkAge  = world?.world?.is_dark_age || false;
  }

  _getTerritoryCenter(name) {
    const layout = {
      "The Void":         { x: 14,  y: 29 },
      "Free Haven":       { x: 27,  y: 17 },
      "The Forge":        { x: 47,  y: 11 },
      "Darkwood":         { x: 15,  y: 43 },
      "The Sunken Keep":  { x: 49,  y: 43 },
      "The Ashfields":    { x: 34,  y: 34 },
      "Crystal Depths":   { x: 10,  y: 10 },
      "Iron Citadel":     { x: 56,  y: 26 },
    };
    const ts   = this.tilemap.tileSize;
    const pos  = layout[name] || layout["Free Haven"];
    return { x: pos.x * ts, y: pos.y * ts };
  }

  _onWorldClick(e) {
    // Find clicked entity
    const wx = e.wx, wy = e.wy;

    // Check characters
    for (const char of this.characters) {
      if (Math.abs(char.x - wx) < 20 && Math.abs(char.y - wy) < 30) {
        this._selected = char;
        this._showInfoPanel("character", char.data);
        this.engine.particles.burst(char.x, char.y - 20, { color: "#ffd700", count: 8, speed: 50 });
        return;
      }
    }

    // Check monsters
    for (const mon of this.monsters) {
      if (Math.abs(mon.x - wx) < 24 && Math.abs(mon.y - wy) < 32) {
        this._selected = mon;
        this._showInfoPanel("monster", mon.data);
        this.engine.particles.burst(mon.x, mon.y - 20, { color: "#cc0000", count: 8, speed: 50 });
        return;
      }
    }

    // Close panel
    this._infoPanel = null;
    this._selected  = null;
  }

  _showInfoPanel(type, data) {
    this._infoPanel = { type, data, timer: 0 };
  }

  update(dt) {
    this.tilemap?.update(dt);
    this.updateEntities(dt);

    // Day/night transition
    const targetAlpha = (this._isNight || this._isDarkAge) ?
                        (this._isDarkAge ? 0.55 : 0.35) : 0;
    this._nightAlpha += (targetAlpha - this._nightAlpha) * Math.min(dt * 0.5, 1);

    if (this._infoPanel) this._infoPanel.timer += dt;
  }

  render(ctx) {
    this.tilemap?.render(ctx);
    this.renderEntities(ctx);

    // Night overlay (in world space)
    if (this._nightAlpha > 0.01) {
      ctx.fillStyle = `rgba(0,0,20,${this._nightAlpha})`;
      ctx.fillRect(0, 0, this.tilemap.pixelWidth, this.tilemap.pixelHeight);
    }
  }

  renderUI(ctx) {
    const W = this.engine.canvas.width;
    const H = this.engine.canvas.height;
    const d = this._uiData;

    // ── Top HUD ──
    ctx.fillStyle = "rgba(6,6,15,0.92)";
    ctx.fillRect(0, 0, W, 36);
    ctx.fillStyle = "#333";
    ctx.fillRect(0, 36, W, 1);

    ctx.font = "bold 8px 'Press Start 2P', monospace";
    ctx.fillStyle = "#ffd700";
    ctx.fillText("⚔ NIXON RPG", 12, 23);

    ctx.font = "13px 'VT323', monospace";
    ctx.fillStyle = "#888";
    let hx = 160;
    const hudItems = [
      { label: "CHAPTER", val: d?.chapter?.id || 1,              color: "#aaaacc" },
      { label: "MONSTERS",val: d?.stats?.monsters_alive || 0,    color: "#cc4444" },
      { label: "HEROES",  val: d?.stats?.total_characters || 0,  color: "#ffd700" },
      { label: "INFRA",   val: `${d?.world?.infrastructure_score||0}/100`, color: "#44cc88" },
    ];
    hudItems.forEach(item => {
      ctx.fillStyle = "#555";
      ctx.fillText(item.label + ": ", hx, 23);
      ctx.fillStyle = item.color;
      ctx.fillText(item.val, hx + ctx.measureText(item.label + ": ").width, 23);
      hx += 110;
    });

    // Dark age badge
    if (this._isDarkAge) {
      ctx.fillStyle = "#cc0000";
      ctx.fillRect(W - 100, 6, 90, 24);
      ctx.fillStyle = "#fff";
      ctx.font = "7px 'Press Start 2P', monospace";
      ctx.textAlign = "center";
      ctx.fillText("⚠ DARK AGE", W - 55, 22);
      ctx.textAlign = "left";
    }

    // Night badge
    if (this._isNight && !this._isDarkAge) {
      ctx.fillStyle = "rgba(0,0,40,0.8)";
      ctx.fillRect(W - 100, 6, 90, 24);
      ctx.fillStyle = "#8888ff";
      ctx.font = "7px 'Press Start 2P', monospace";
      ctx.textAlign = "center";
      ctx.fillText("🌙 NIGHT", W - 55, 22);
      ctx.textAlign = "left";
    }

    // Chapter name
    ctx.font      = "13px 'VT323', monospace";
    ctx.fillStyle = "#9944ff";
    ctx.textAlign = "right";
    ctx.fillText(d?.chapter?.name || "Chapter 1", W - 110, 23);
    ctx.textAlign = "left";

    // FPS (debug)
    if (this.engine.config.debug) {
      ctx.fillStyle = "#00ff00";
      ctx.font = "10px monospace";
      ctx.fillText(`FPS: ${this.engine.fps}`, W - 60, H - 10);
    }

    // ── Info Panel ──
    if (this._infoPanel) {
      this._renderInfoPanel(ctx);
    }

    // ── Controls hint ──
    ctx.fillStyle = "rgba(0,0,0,0.5)";
    ctx.fillRect(0, H - 24, W, 24);
    ctx.fillStyle = "#444";
    ctx.font = "11px 'VT323', monospace";
    ctx.fillText("WASD/ARROWS: pan  |  SCROLL: zoom  |  CLICK: select", 12, H - 8);
  }

  _renderInfoPanel(ctx) {
    const W = this.engine.canvas.width;
    const { type, data } = this._infoPanel;
    const pw = 220, ph = type === "monster" ? 130 : 150;
    const px = W - pw - 12;
    const py = 48;

    // Panel background
    ctx.fillStyle = "rgba(10,10,20,0.95)";
    ctx.fillRect(px, py, pw, ph);
    ctx.strokeStyle = type === "monster" ? "#cc0000" : "#ffd700";
    ctx.lineWidth   = 1;
    ctx.strokeRect(px, py, pw, ph);

    ctx.font      = "bold 7px 'Press Start 2P', monospace";
    ctx.fillStyle = type === "monster" ? "#ff4444" : "#ffd700";
    ctx.fillText((data.name || "?").slice(0, 20), px + 10, py + 18);

    ctx.font      = "13px 'VT323', monospace";
    ctx.fillStyle = "#888";
    let ly = py + 34;
    const lineH = 18;

    if (type === "character") {
      const s = data.stats || {};
      [
        ["TYPE",      data.is_nft ? "🔮 NFT HERO" : "⚔ HERO"],
        ["ARCHETYPE", (data.archetype||"?").toUpperCase()],
        ["STR",       s.strength || 0],
        ["WIS",       s.wisdom   || 0],
        ["LEGACY",    s.legacy   || 0],
        ["TERRITORY", (data.territory||"?").slice(0,18)],
      ].forEach(([label, val]) => {
        ctx.fillStyle = "#555";
        ctx.fillText(label + ": ", px + 10, ly);
        ctx.fillStyle = "#ccc";
        ctx.fillText(val, px + 80, ly);
        ly += lineH;
      });
    } else {
      const pct = Math.round(((data.hp||0)/(data.max_hp||1))*100);
      [
        ["TIER",  (data.tier||"?").toUpperCase() + (data.is_boss?" 👑":"")],
        ["HP",    `${data.hp||0}/${data.max_hp||0} (${pct}%)`],
        ["ZONE",  (data.territory||"?").slice(0,18)],
      ].forEach(([label, val]) => {
        ctx.fillStyle = "#555";
        ctx.fillText(label + ": ", px + 10, ly);
        ctx.fillStyle = label === "HP" ? "#cc4444" : "#ccc";
        ctx.fillText(val, px + 60, ly);
        ly += lineH;
      });
      // HP bar
      const bw = pw - 20;
      ctx.fillStyle = "#330000";
      ctx.fillRect(px + 10, ly, bw, 6);
      ctx.fillStyle = "#cc2200";
      ctx.fillRect(px + 10, ly, bw * (pct/100), 6);
    }
  }
}
