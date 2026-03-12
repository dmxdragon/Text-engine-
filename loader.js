/**
 * Nixon RPG Engine — Advanced Asset Manager
 * Manifest loading, caching, groups, error handling, progress screen
 */

class AssetLoader {
  constructor(engine) {
    this.engine   = engine;
    this.images   = {};
    this.sheets   = {};
    this.tilemaps = {};
    this.audio    = {};
    this._queue   = [];
    this._loaded  = 0;
    this._total   = 0;
    this._cache   = {};
  }

  // ─────────────────────────────────────────────
  // QUEUE API
  // ─────────────────────────────────────────────
  image(name, path) {
    this._queue.push({ type:"image", name, path });
    return this;
  }

  spriteSheet(name, path, frameW, frameH, animations = {}) {
    this._queue.push({ type:"sheet", name, path, frameW, frameH, animations });
    return this;
  }

  tileset(name, path, tileW, tileH) {
    this._queue.push({ type:"tileset", name, path, tileW, tileH });
    return this;
  }

  sound(name, path) {
    this._queue.push({ type:"audio", name, path });
    return this;
  }

  // Load from manifest JSON object
  manifest(data) {
    Object.entries(data.images   || {}).forEach(([n,p])   => this.image(n, p));
    Object.entries(data.sounds   || {}).forEach(([n,p])   => this.sound(n, p));
    Object.entries(data.tilesets || {}).forEach(([n,cfg]) => this.tileset(n, cfg.path, cfg.tileW||32, cfg.tileH||32));
    Object.entries(data.sheets   || {}).forEach(([n,cfg]) => this.spriteSheet(n, cfg.path, cfg.frameW||32, cfg.frameH||32, cfg.animations||{}));
    return this;
  }

  // ─────────────────────────────────────────────
  // LOAD
  // ─────────────────────────────────────────────
  async loadAll(onProgress = null) {
    this._total  = this._queue.length;
    this._loaded = 0;
    if (this._total === 0) { this.engine.emit("loader:complete", { loaded:0, total:0 }); return; }

    const promises = this._queue.map(async (asset) => {
      try {
        await this._loadAsset(asset);
      } catch(e) {
        console.warn(`[Loader] ⚠ ${asset.name} failed — placeholder used`);
        this._createPlaceholder(asset);
      }
      this._loaded++;
      const progress = this._loaded / this._total;
      onProgress?.(progress, asset.name);
      this.engine.emit("loader:progress", { progress, name:asset.name });
    });

    await Promise.all(promises);
    this._queue = [];
    this.engine.emit("loader:complete", { loaded:this._loaded, total:this._total });
    console.log(`[Loader] ✓ ${this._loaded}/${this._total} assets`);
  }

  async _loadAsset(asset) {
    switch(asset.type) {
      case "image":   await this._loadImage(asset);   break;
      case "sheet":   await this._loadSheet(asset);   break;
      case "tileset": await this._loadTileset(asset); break;
      case "audio":   await this._loadAudio(asset);   break;
    }
  }

  _loadImage(asset) {
    if (this._cache[asset.path]) {
      this.images[asset.name] = this._cache[asset.path];
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload  = () => { this.images[asset.name] = img; this._cache[asset.path] = img; resolve(); };
      img.onerror = () => reject(new Error(`Not found: ${asset.path}`));
      img.src = asset.path;
    });
  }

  async _loadSheet(asset) {
    const tmpKey = "__tmp_" + asset.path;
    await this._loadImage({ name: tmpKey, path: asset.path });
    const img = this._cache[asset.path] || this.images[tmpKey];
    this.sheets[asset.name] = new SpriteSheet(img, asset.frameW, asset.frameH, asset.animations);
    this.images[asset.name] = img;
  }

  async _loadTileset(asset) {
    const tmpKey = "__tmp_" + asset.path;
    await this._loadImage({ name: tmpKey, path: asset.path });
    const img = this._cache[asset.path] || this.images[tmpKey];
    this.tilemaps[asset.name] = new Tileset(img,
      asset.tileW || this.engine.config.tileSize,
      asset.tileH || this.engine.config.tileSize
    );
    this.images[asset.name] = img;
  }

  async _loadAudio(asset) {
    try {
      const ctx = this.engine.audio?.context;
      if (!ctx) return;
      const res = await fetch(asset.path);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = await res.arrayBuffer();
      this.audio[asset.name] = await ctx.decodeAudioData(buf);
    } catch(e) {
      console.warn(`[Loader] Audio failed: ${asset.name}`);
    }
  }

  // Checkerboard placeholder for missing assets
  _createPlaceholder(asset) {
    const size = 64;
    const c    = document.createElement("canvas");
    c.width = c.height = size;
    const ctx  = c.getContext("2d");
    const col  = { image:"#ff00ff", sheet:"#ff8800", tileset:"#00ff88" }[asset.type] || "#444";
    for (let y=0;y<4;y++) for (let x=0;x<4;x++) {
      ctx.fillStyle = (x+y)%2===0 ? col : "#111";
      ctx.fillRect(x*16, y*16, 16, 16);
    }
    ctx.fillStyle="#fff"; ctx.font="bold 28px sans-serif";
    ctx.textAlign="center"; ctx.fillText("?",32,44);
    const img = new Image(); img.src = c.toDataURL();
    this.images[asset.name] = img;
    if (asset.type==="sheet")   this.sheets[asset.name]   = new SpriteSheet(img, asset.frameW||32, asset.frameH||32, asset.animations||{});
    if (asset.type==="tileset") this.tilemaps[asset.name] = new Tileset(img, 32, 32);
  }

  getImage(name)   { return this.images[name]   || null; }
  getSheet(name)   { return this.sheets[name]   || null; }
  getTileset(name) { return this.tilemaps[name] || null; }
  getAudio(name)   { return this.audio[name]    || null; }
  get progress()   { return this._total > 0 ? this._loaded / this._total : 1; }
}

// ═══════════════════════════════════════════════════════════
// SPRITE SHEET
// ═══════════════════════════════════════════════════════════
class SpriteSheet {
  constructor(image, frameW, frameH, animations = {}) {
    this.image      = image;
    this.frameW     = frameW;
    this.frameH     = frameH;
    this.cols       = Math.max(1, Math.floor((image.width ||32) / frameW));
    this.rows       = Math.max(1, Math.floor((image.height||32) / frameH));
    this.animations = {};
    Object.entries(animations).forEach(([n,c]) =>
      this.addAnimation(n, c.row, c.frames, c.fps||8, c.loop!==false)
    );
  }

  addAnimation(name, row, frameCount, fps=8, loop=true) {
    this.animations[name] = { row, frameCount, fps, loop };
    return this;
  }

  draw(ctx, animName, frameIndex, x, y, w, h, flipX=false) {
    const anim = this.animations[animName];
    if (!anim) return;
    const col = Math.floor(frameIndex) % anim.frameCount;
    const sx  = col * this.frameW;
    const sy  = anim.row * this.frameH;
    const dw  = w || this.frameW;
    const dh  = h || this.frameH;
    if (flipX) {
      ctx.save(); ctx.scale(-1,1);
      ctx.drawImage(this.image, sx, sy, this.frameW, this.frameH, -(x+dw), y, dw, dh);
      ctx.restore();
    } else {
      ctx.drawImage(this.image, sx, sy, this.frameW, this.frameH, x, y, dw, dh);
    }
  }
}

// ═══════════════════════════════════════════════════════════
// TILESET
// ═══════════════════════════════════════════════════════════
class Tileset {
  constructor(image, tileW, tileH) {
    this.image = image;
    this.tileW = tileW;
    this.tileH = tileH;
    this.cols  = Math.max(1, Math.floor((image.width ||32) / tileW));
    this.rows  = Math.max(1, Math.floor((image.height||32) / tileH));
  }

  drawTile(ctx, tileIndex, dx, dy, w, h) {
    if (tileIndex < 0) return;
    const col = tileIndex % this.cols;
    const row = Math.floor(tileIndex / this.cols);
    ctx.drawImage(this.image,
      col*this.tileW, row*this.tileH, this.tileW, this.tileH,
      dx, dy, w||this.tileW, h||this.tileH
    );
  }

  drawTileRC(ctx, row, col, dx, dy, w, h) {
    ctx.drawImage(this.image,
      col*this.tileW, row*this.tileH, this.tileW, this.tileH,
      dx, dy, w||this.tileW, h||this.tileH
    );
  }
}
