/**
 * Nixon RPG Engine — TileMap + Building System
 * supports image-based tiles (128×128, 64×128, 128×64, etc.)
 * and procedural building generation from pieces
 */

// ═══════════════════════════════════════════════════════════
// TILE MAP
// ═══════════════════════════════════════════════════════════
class TileMap {
  constructor(engine, config = {}) {
    this.engine   = engine;
    this.tileSize = config.tileSize || engine.config.tileSize; // base grid = 32
    this.width    = config.width    || 80;
    this.height   = config.height   || 60;

    // Image-based tile registry (name → Image)
    // supports any size — engine scales to tileSize grid
    this._tileImages = {};

    // Layers
    this.layers = {
      ground:    this._createLayer(), // terrain tiles
      detail:    this._createLayer(), // rocks, flowers
      buildings: [],                  // building instances (free-placed)
      objects:   [],                  // barrels, torches, etc.
      nature:    [],                  // trees, rocks
      overlay:   this._createLayer(), // roofs, bridges
    };

    // Collision map
    this.collision = new Uint8Array(this.width * this.height);

    // Territory zones
    this.territories = {};

    // Animated objects { name, x, y, frames, fps, timer, current }
    this._animated = [];
  }

  _createLayer() {
    return new Int16Array(this.width * this.height).fill(-1);
  }

  // ─────────────────────────────────────────────
  // REGISTER TILES FROM IMAGES
  // engine.loader.getImage("grass") → register
  // ─────────────────────────────────────────────
  registerTile(name, imageName) {
    const img = this.engine.loader.getImage(imageName);
    if (img) this._tileImages[name] = img;
    return this;
  }

  // Register all tiles at once from manifest
  // { grass: "grass", stone: "stone", ... }
  registerTiles(map) {
    Object.entries(map).forEach(([name, imgName]) => this.registerTile(name, imgName));
    return this;
  }

  // ─────────────────────────────────────────────
  // TILE PLACEMENT (by name)
  // ─────────────────────────────────────────────
  setTileByName(layer, x, y, tileName) {
    if (x < 0 || x >= this.width || y < 0 || y >= this.height) return;
    // Store name index — use _tileNames array
    if (!this._tileNames) this._tileNames = [];
    let idx = this._tileNames.indexOf(tileName);
    if (idx === -1) { idx = this._tileNames.length; this._tileNames.push(tileName); }
    this.layers[layer][y * this.width + x] = idx;
  }

  setTile(layer, x, y, tileIndex) {
    if (x < 0 || x >= this.width || y < 0 || y >= this.height) return;
    if (typeof layer === "string" && Array.isArray(this.layers[layer])) return; // skip object layers
    this.layers[layer][y * this.width + x] = tileIndex;
  }

  fill(layer, x, y, w, h, tileName) {
    for (let ty = y; ty < y + h; ty++)
      for (let tx = x; tx < x + w; tx++)
        this.setTileByName(layer, tx, ty, tileName);
  }

  // ─────────────────────────────────────────────
  // COLLISION
  // ─────────────────────────────────────────────
  setCollision(x, y, solid = true) {
    if (x < 0 || x >= this.width || y < 0 || y >= this.height) return;
    this.collision[y * this.width + x] = solid ? 1 : 0;
  }

  isSolid(x, y) {
    if (x < 0 || x >= this.width || y < 0 || y >= this.height) return true;
    return this.collision[y * this.width + x] === 1;
  }

  isSolidAt(px, py) {
    return this.isSolid(Math.floor(px / this.tileSize), Math.floor(py / this.tileSize));
  }

  // ─────────────────────────────────────────────
  // OBJECT / BUILDING PLACEMENT
  // Place an image asset at exact pixel position
  // supports any size (128×128, 64×128, etc.)
  // ─────────────────────────────────────────────
  placeObject(imageName, px, py, opts = {}) {
    const img = this.engine.loader.getImage(imageName);
    if (!img) return null;

    const obj = {
      image:   img,
      name:    imageName,
      x:       px,
      y:       py,
      w:       opts.w       || img.width  || 128,
      h:       opts.h       || img.height || 128,
      scale:   opts.scale   || 1,
      solid:   opts.solid   !== false,
      layer:   opts.layer   || "objects",
      alpha:   opts.alpha   || 1,
      flipX:   opts.flipX   || false,
      // animation support
      frames:  opts.frames  || null,  // [img1, img2, ...] for animated objects
      fps:     opts.fps     || 4,
      _timer:  0,
      _frame:  0,
    };

    // Add to correct layer
    if (obj.layer === "buildings") this.layers.buildings.push(obj);
    else if (obj.layer === "nature") this.layers.nature.push(obj);
    else this.layers.objects.push(obj);

    // Mark collision tiles
    if (obj.solid) {
      const ts  = this.tileSize;
      const tx1 = Math.floor(px / ts);
      const ty1 = Math.floor(py / ts);
      const tx2 = Math.floor((px + obj.w * obj.scale) / ts);
      const ty2 = Math.floor((py + obj.h * obj.scale) / ts);
      for (let ty = ty1; ty <= ty2; ty++)
        for (let tx = tx1; tx <= tx2; tx++)
          this.setCollision(tx, ty, true);
    }

    return obj;
  }

  placeBuilding(imageName, px, py, opts = {}) {
    return this.placeObject(imageName, px, py, { ...opts, layer:"buildings", solid:true });
  }

  placeNature(imageName, px, py, opts = {}) {
    return this.placeObject(imageName, px, py, { ...opts, layer:"nature", solid:opts.solid||false });
  }

  // ─────────────────────────────────────────────
  // TERRITORY ZONES
  // ─────────────────────────────────────────────
  setTerritoryZone(name, x, y, w, h, state = "neutral") {
    this.territories[name] = { x, y, w, h, state };
  }

  updateTerritoryState(name, state) {
    if (this.territories[name]) this.territories[name].state = state;
  }

  // ─────────────────────────────────────────────
  // UPDATE
  // ─────────────────────────────────────────────
  update(dt) {
    // Animate objects
    const allObjs = [...this.layers.objects, ...this.layers.buildings, ...this.layers.nature];
    allObjs.forEach(obj => {
      if (!obj.frames) return;
      obj._timer += dt;
      if (obj._timer >= 1 / obj.fps) {
        obj._timer = 0;
        obj._frame = (obj._frame + 1) % obj.frames.length;
        obj.image  = obj.frames[obj._frame];
      }
    });
  }

  // ─────────────────────────────────────────────
  // RENDER
  // ─────────────────────────────────────────────
  render(ctx) {
    const cam    = this.engine.camera;
    const ts     = this.tileSize;
    const startX = Math.max(0, Math.floor(cam.x / ts));
    const startY = Math.max(0, Math.floor(cam.y / ts));
    const endX   = Math.min(this.width,  startX + Math.ceil(this.engine.config.width  / (ts * cam.zoom)) + 3);
    const endY   = Math.min(this.height, startY + Math.ceil(this.engine.config.height / (ts * cam.zoom)) + 3);

    // 1. Territory overlays
    this._renderTerritories(ctx, ts);

    // 2. Ground layer (image-based tiles)
    this._renderTileLayer(ctx, "ground",  startX, startY, endX, endY, ts);
    this._renderTileLayer(ctx, "detail",  startX, startY, endX, endY, ts);

    // 3. Nature (behind buildings)
    this._renderObjectLayer(ctx, this.layers.nature,    cam);

    // 4. Buildings
    this._renderObjectLayer(ctx, this.layers.buildings, cam);

    // 5. Objects (barrels, torches, etc.)
    this._renderObjectLayer(ctx, this.layers.objects,   cam);

    // 6. Overlay
    this._renderTileLayer(ctx, "overlay", startX, startY, endX, endY, ts);

    // Debug
    if (this.engine.debug?.enabled) this._renderDebug(ctx, startX, startY, endX, endY, ts);
  }

  _renderTileLayer(ctx, layerName, startX, startY, endX, endY, ts) {
    const layer = this.layers[layerName];
    if (!layer || Array.isArray(layer)) return;

    for (let y = startY; y < endY; y++) {
      for (let x = startX; x < endX; x++) {
        const idx = layer[y * this.width + x];
        if (idx < 0) continue;

        const name = this._tileNames?.[idx];
        const img  = name ? this._tileImages[name] : null;

        if (img) {
          // Image-based tile — scale to tileSize
          ctx.drawImage(img, x * ts, y * ts, ts, ts);
        } else {
          // Fallback color
          const colors = ["#1a2e1a","#2a2a1a","#0d1a2e","#1a1a1a","#2e1a1a"];
          ctx.fillStyle = colors[idx % colors.length] || "#0d0d0d";
          ctx.fillRect(x * ts, y * ts, ts, ts);
        }
      }
    }
  }

  _renderObjectLayer(ctx, objects, cam) {
    if (!objects?.length) return;

    // Sort by Y (painter's algorithm)
    const sorted = [...objects].sort((a, b) => (a.y + a.h) - (b.y + b.h));

    sorted.forEach(obj => {
      if (!obj.image) return;

      const dw = obj.w * obj.scale;
      const dh = obj.h * obj.scale;

      // Frustum cull
      const sx = (obj.x - cam.x) * cam.zoom;
      const sy = (obj.y - cam.y) * cam.zoom;
      if (sx + dw * cam.zoom < 0 || sx > this.engine.config.width)  return;
      if (sy + dh * cam.zoom < 0 || sy > this.engine.config.height) return;

      ctx.globalAlpha = obj.alpha;

      if (obj.flipX) {
        ctx.save();
        ctx.scale(-1, 1);
        ctx.drawImage(obj.image, -(obj.x + dw), obj.y, dw, dh);
        ctx.restore();
      } else {
        ctx.drawImage(obj.image, obj.x, obj.y, dw, dh);
      }

      ctx.globalAlpha = 1;
    });
  }

  _renderTerritories(ctx, ts) {
    const stateColors = {
      controlled:    "rgba(200,0,0,0.10)",
      liberated:     "rgba(0,200,68,0.10)",
      neutral:       "rgba(50,50,100,0.06)",
      law_protected: "rgba(68,136,255,0.12)",
      contested:     "rgba(255,136,0,0.10)",
    };
    const borderColors = {
      controlled:"#cc0000", liberated:"#00cc44",
      neutral:"#333366", law_protected:"#4488ff", contested:"#ff8800"
    };

    Object.entries(this.territories).forEach(([name, zone]) => {
      ctx.fillStyle   = stateColors[zone.state] || stateColors.neutral;
      ctx.fillRect(zone.x*ts, zone.y*ts, zone.w*ts, zone.h*ts);
      ctx.strokeStyle = borderColors[zone.state] || "#333366";
      ctx.lineWidth   = 2;
      ctx.strokeRect(zone.x*ts, zone.y*ts, zone.w*ts, zone.h*ts);
      ctx.fillStyle   = "#ffffff";
      ctx.globalAlpha = 0.5;
      ctx.font        = "bold 11px 'VT323', monospace";
      ctx.textAlign   = "center";
      ctx.fillText(name.toUpperCase(), (zone.x + zone.w/2)*ts, (zone.y + zone.h/2)*ts);
      ctx.textAlign   = "left";
      ctx.globalAlpha = 1;
    });
  }

  _renderDebug(ctx, startX, startY, endX, endY, ts) {
    ctx.strokeStyle = "rgba(255,255,255,0.05)";
    ctx.lineWidth   = 0.5;
    for (let x=startX; x<endX; x++) { ctx.beginPath(); ctx.moveTo(x*ts,startY*ts); ctx.lineTo(x*ts,endY*ts); ctx.stroke(); }
    for (let y=startY; y<endY; y++) { ctx.beginPath(); ctx.moveTo(startX*ts,y*ts); ctx.lineTo(endX*ts,y*ts); ctx.stroke(); }
    for (let y=startY; y<endY; y++) {
      for (let x=startX; x<endX; x++) {
        if (this.isSolid(x,y)) {
          ctx.fillStyle = "rgba(255,0,0,0.2)";
          ctx.fillRect(x*ts+1, y*ts+1, ts-2, ts-2);
        }
      }
    }
  }

  get pixelWidth()  { return this.width  * this.tileSize; }
  get pixelHeight() { return this.height * this.tileSize; }
}

// ═══════════════════════════════════════════════════════════
// WORLD GENERATOR
// builds world from image pieces procedurally
// ═══════════════════════════════════════════════════════════
class WorldGenerator {
  constructor(engine, tilemap) {
    this.engine  = engine;
    this.tilemap = tilemap;
  }

  // Generate full world from API data + available assets
  generate(worldData = {}) {
    const map  = this.tilemap;
    const ts   = map.tileSize;
    const W    = map.width;
    const H    = map.height;

    // 1. Register available tile images
    map.registerTiles({
      grass:       "grass",
      stone:       "stone",
      dirt:        "dirt",
      water:       "water",
      dark_ground: "dark_ground",
    });

    // 2. Base terrain
    map.fill("ground", 0, 0, W, H, "grass");

    // 3. Territory zones + terrain
    const territories = worldData.territories || {};
    const zoneLayout  = this._getZoneLayout();

    Object.entries(zoneLayout).forEach(([name, zone]) => {
      const state   = territories[name]?.state || "neutral";
      const terrain = this._getTerrain(name, state);

      map.setTerritoryZone(name, zone.x, zone.y, zone.w, zone.h, state);
      map.fill("ground", zone.x, zone.y, zone.w, zone.h, terrain);

      // Place buildings in zone
      this._buildZone(name, zone, state, ts);
    });

    // 4. Water borders
    this._addWater(W, H);

    // 5. Nature (trees, rocks) scattered
    this._scatterNature(W, H, ts);

    return this;
  }

  _getTerrain(zoneName, state) {
    const terrainMap = {
      "The Void":        "dark_ground",
      "Darkwood":        "dark_ground",
      "The Forge":       "stone",
      "The Sunken Keep": "stone",
      "Free Haven":      "grass",
      "The Ashfields":   "dirt",
      "Crystal Depths":  "stone",
      "Iron Citadel":    "stone",
    };
    if (state === "controlled") return "dark_ground";
    return terrainMap[zoneName] || "grass";
  }

  _buildZone(name, zone, state, ts) {
    const map  = this.tilemap;
    const cx   = (zone.x + zone.w / 2) * ts;
    const cy   = (zone.y + zone.h / 2) * ts;
    const size = 128; // building piece size

    const themes = {
      "Free Haven":      { wall:"wooden_wall",  door:"wooden_door",  fence:"wooden_fence", tower:null },
      "The Forge":       { wall:"stone_wall",   door:"iron_gate",    fence:null,           tower:"stone_tower" },
      "The Sunken Keep": { wall:"stone_wall",   door:"stone_arch",   fence:null,           tower:"stone_tower" },
      "Darkwood":        { wall:"wooden_fence", door:"wooden_door",  fence:"wooden_fence", tower:null },
      "The Void":        { wall:"stone_wall",   door:"iron_gate",    fence:null,           tower:"stone_tower" },
      "The Ashfields":   { wall:"wooden_wall",  door:"wooden_door",  fence:"wooden_fence", tower:null },
      "Crystal Depths":  { wall:"stone_wall",   door:"stone_arch",   fence:null,           tower:"stone_tower" },
      "Iron Citadel":    { wall:"stone_wall",   door:"iron_gate",    fence:null,           tower:"stone_tower" },
    };

    const theme = themes[name] || themes["Free Haven"];

    // Place walls around center
    if (theme.wall) {
      // Top wall
      for (let i = -1; i <= 1; i++) {
        if (i === 0 && theme.door) {
          map.placeBuilding(theme.door,  cx + i*size - size/2, cy - size*1.5, { w:size, h:size });
        } else {
          map.placeBuilding(theme.wall,  cx + i*size - size/2, cy - size*1.5, { w:size, h:size });
        }
        map.placeBuilding(theme.wall, cx + i*size - size/2, cy + size*0.5, { w:size, h:size });
      }
      // Side walls
      map.placeBuilding(theme.wall, cx - size*1.5, cy - size*0.5, { w:size, h:size });
      map.placeBuilding(theme.wall, cx + size*0.5, cy - size*0.5, { w:size, h:size });
    }

    // Tower at corner
    if (theme.tower) {
      map.placeBuilding(theme.tower, cx - size*2, cy - size*2, { w:size, h:size*1.5 });
    }

    // Objects inside
    const hasChest    = this.engine.loader.getImage("chest");
    const hasCampfire = this.engine.loader.getImage("campfire");
    const hasBarrel   = this.engine.loader.getImage("barrel");
    const hasTorch    = this.engine.loader.getImage("torch");

    if (hasCampfire) map.placeObject("campfire", cx - 32, cy, { w:64, h:64, solid:false });
    if (hasBarrel)   map.placeObject("barrel",   cx + 80, cy + 20, { w:64, h:64, solid:false });
    if (hasTorch)    map.placeObject("torch",    cx - size*1.4, cy - size*1.2, { w:32, h:64, solid:false });
    if (hasChest && state === "liberated") {
      map.placeObject("chest", cx, cy - 40, { w:64, h:64, solid:true });
    }
  }

  _addWater(W, H) {
    const map = this.tilemap;
    // Water edges
    map.fill("ground", 0,   0,   W,   3,   "water");
    map.fill("ground", 0,   H-3, W,   3,   "water");
    map.fill("ground", 0,   0,   3,   H,   "water");
    map.fill("ground", W-3, 0,   3,   H,   "water");
    // Set collision for water
    for (let y=0; y<H; y++) for (let x=0; x<W; x++) {
      const idx = map.layers.ground[y*W+x];
      const name = map._tileNames?.[idx];
      if (name === "water") map.setCollision(x, y, true);
    }
  }

  _scatterNature(W, H, ts) {
    const map      = this.tilemap;
    const hasTree  = this.engine.loader.getImage("tree");
    const hasRock  = this.engine.loader.getImage("rock");
    const hasBush  = this.engine.loader.getImage("bush");

    const count = 60;
    for (let i = 0; i < count; i++) {
      const x = (4 + Math.random() * (W - 8)) * ts;
      const y = (4 + Math.random() * (H - 8)) * ts;
      if (map.isSolidAt(x, y)) continue;

      const r = Math.random();
      if (r < 0.5 && hasTree) {
        map.placeNature("tree", x, y - 64, { w:64, h:128, solid:true });
      } else if (r < 0.75 && hasRock) {
        map.placeNature("rock", x, y, { w:64, h:64, solid:true });
      } else if (hasBush) {
        map.placeNature("bush", x, y + 16, { w:64, h:48, solid:false });
      }
    }
  }

  _getZoneLayout() {
    return {
      "The Void":        { x:2,  y:22, w:14, h:12 },
      "Free Haven":      { x:20, y:10, w:14, h:12 },
      "The Forge":       { x:40, y:4,  w:14, h:12 },
      "Darkwood":        { x:8,  y:36, w:14, h:12 },
      "The Sunken Keep": { x:42, y:36, w:14, h:12 },
      "The Ashfields":   { x:28, y:26, w:12, h:12 },
      "Crystal Depths":  { x:4,  y:4,  w:12, h:12 },
      "Iron Citadel":    { x:52, y:20, w:12, h:12 },
    };
  }
}
