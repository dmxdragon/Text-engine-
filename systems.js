/**
 * Nixon RPG Engine — Core Systems
 * Camera, Physics, Particles, API Bridge
 */

// ═══════════════════════════════════════════════════════════
// CAMERA
// ═══════════════════════════════════════════════════════════
class Camera {
  constructor(engine) {
    this.engine    = engine;
    this.x         = 0;
    this.y         = 0;
    this.zoom      = 1.5;
    this.minZoom   = 0.4;
    this.maxZoom   = 5.0;
    this.lerpSpeed = 6;
    this._targetX  = 0;
    this._targetY  = 0;
    this._following= null;
    this._shakeX   = 0;
    this._shakeY   = 0;
    this._shakeMag = 0;
    this._shakeDur = 0;
    this._shakeT   = 0;
    this.bounds    = null;
  }

  follow(entity)      { this._following = entity; }
  stopFollowing()     { this._following = null; }

  panTo(x, y, instant=false) {
    this._targetX = x; this._targetY = y;
    if (instant) { this.x = x; this.y = y; }
  }

  shake(magnitude=8, duration=0.3) {
    this._shakeMag = magnitude;
    this._shakeDur = duration;
    this._shakeT   = 0;
  }

  setZoom(z) {
    this.zoom = Math.max(this.minZoom, Math.min(this.maxZoom, z));
  }

  setBounds(x, y, w, h) { this.bounds = { x, y, w, h }; }

  update(dt) {
    if (this._following) {
      this._targetX = this._following.x - this.engine.config.width  / (2 * this.zoom);
      this._targetY = this._following.y - this.engine.config.height / (2 * this.zoom);
    }

    // Smooth lerp
    const t = Math.min(this.lerpSpeed * dt, 1);
    this.x += (this._targetX - this.x) * t;
    this.y += (this._targetY - this.y) * t;

    // Bounds clamp
    if (this.bounds) {
      const maxX = this.bounds.w - this.engine.config.width  / this.zoom;
      const maxY = this.bounds.h - this.engine.config.height / this.zoom;
      this.x = Math.max(this.bounds.x, Math.min(maxX, this.x));
      this.y = Math.max(this.bounds.y, Math.min(maxY, this.y));
    }

    // Shake
    if (this._shakeT < this._shakeDur) {
      this._shakeT += dt;
      const intensity = 1 - this._shakeT / this._shakeDur;
      this._shakeX = (Math.random()-0.5) * this._shakeMag * intensity * 2;
      this._shakeY = (Math.random()-0.5) * this._shakeMag * intensity * 2;
    } else {
      this._shakeX = this._shakeY = 0;
    }
  }

  applyTransform(ctx) {
    ctx.translate(
      -this.x * this.zoom + this._shakeX,
      -this.y * this.zoom + this._shakeY
    );
    ctx.scale(this.zoom, this.zoom);
  }

  screenToWorld(sx, sy) {
    return { x: sx / this.zoom + this.x, y: sy / this.zoom + this.y };
  }

  worldToScreen(wx, wy) {
    return { x: (wx - this.x) * this.zoom, y: (wy - this.y) * this.zoom };
  }
}

// ═══════════════════════════════════════════════════════════
// PHYSICS ENGINE
// ═══════════════════════════════════════════════════════════
class PhysicsEngine {
  constructor(engine) {
    this.engine   = engine;
    this.gravity  = 0;
    this._entities= [];
    this._fixed   = 1/60;
    this._accum   = 0;
  }

  register(entity) {
    if (!this._entities.includes(entity)) this._entities.push(entity);
  }

  unregister(entity) {
    this._entities = this._entities.filter(e => e !== entity);
  }

  update(dt) {
    // Fixed timestep accumulator
    const scaledDt = this.engine.time ? this.engine.time.scale(dt) : dt;
    this._accum   += scaledDt;

    while (this._accum >= this._fixed) {
      this._step(this._fixed);
      this._accum -= this._fixed;
    }
  }

  _step(dt) {
    const map = this.engine.scenes?.current?.tilemap;

    this._entities.forEach(e => {
      if (!e.physics || e.dead || e.static) return;

      e.x += e.vx * dt;
      e.y += e.vy * dt;

      // Friction
      e.vx *= (1 - Math.min((e.friction||0.85), 1));
      e.vy *= (1 - Math.min((e.friction||0.85), 1));
      if (Math.abs(e.vx) < 0.1) e.vx = 0;
      if (Math.abs(e.vy) < 0.1) e.vy = 0;

      // Tile collision
      if (map && e.collides) this._resolveTile(e, map);
    });

    // Entity vs entity
    this._resolveEntities();
  }

  _resolveTile(e, map) {
    const ts = map.tileSize;
    const hw = (e.w||ts) * 0.4;
    const hh = (e.h||ts) * 0.4;
    const pts = [
      { x:e.x-hw+1, y:e.y-hh+1 }, { x:e.x+hw-1, y:e.y-hh+1 },
      { x:e.x-hw+1, y:e.y+hh-1 }, { x:e.x+hw-1, y:e.y+hh-1 },
    ];
    pts.forEach(p => {
      if (!map.isSolidAt(p.x, p.y)) return;
      const tx = Math.floor(p.x/ts)*ts, ty = Math.floor(p.y/ts)*ts;
      const ox = e.x > tx+ts/2 ? (tx+ts)-(e.x-hw) : tx-(e.x+hw);
      const oy = e.y > ty+ts/2 ? (ty+ts)-(e.y-hh) : ty-(e.y+hh);
      if (Math.abs(ox) < Math.abs(oy)) { e.x += ox; e.vx = 0; }
      else                             { e.y += oy; e.vy = 0; }
    });
  }

  _resolveEntities() {
    const len = this._entities.length;
    for (let i=0; i<len; i++) {
      const a = this._entities[i];
      if (!a.collides || a.dead) continue;
      for (let j=i+1; j<len; j++) {
        const b = this._entities[j];
        if (!b.collides || b.dead) continue;
        const aw=a.w/2, ah=a.h/2, bw=b.w/2, bh=b.h/2;
        if (Math.abs(a.x-b.x)<aw+bw && Math.abs(a.y-b.y)<ah+bh) {
          this.engine.emit("physics:collision", { a, b });
        }
      }
    }
  }

  distanceBetween(a, b) {
    return Math.sqrt((b.x-a.x)**2 + (b.y-a.y)**2);
  }
}

// ═══════════════════════════════════════════════════════════
// PARTICLE SYSTEM
// ═══════════════════════════════════════════════════════════
class ParticleSystem {
  constructor(engine) {
    this.engine    = engine;
    this._emitters = [];
    this._pool     = [];      // object pool for particles
    this._active   = [];
    this._poolSize = 500;

    // Pre-allocate particle pool
    for (let i=0; i<this._poolSize; i++) {
      this._pool.push(this._makeParticle());
    }
  }

  _makeParticle() {
    return { x:0,y:0,vx:0,vy:0,color:"#fff",size:4,life:1,maxLife:1,gravity:0,shape:"rect",active:false };
  }

  _get() {
    return this._pool.pop() || this._makeParticle();
  }

  _release(p) {
    p.active = false;
    this._pool.push(p);
  }

  // ─────────────────────────────────────────────
  // BURST
  // ─────────────────────────────────────────────
  burst(x, y, cfg={}) {
    const count = cfg.count || 12;
    for (let i=0; i<count; i++) {
      const p = this._get();
      const angle = (Math.PI*2/count)*i + (Math.random()-0.5)*0.8;
      const speed = (cfg.speed||80) * (0.4 + Math.random()*0.6);
      const color = Array.isArray(cfg.color)
        ? cfg.color[Math.floor(Math.random()*cfg.color.length)]
        : (cfg.color || "#ffd700");
      p.x      = x + (Math.random()-0.5) * (cfg.spread||0);
      p.y      = y + (Math.random()-0.5) * (cfg.spread||0);
      p.vx     = Math.cos(angle) * speed;
      p.vy     = Math.sin(angle) * speed;
      p.color  = color;
      p.size   = (cfg.size||4) * (0.5+Math.random()*0.5);
      p.life   = p.maxLife = cfg.life || 0.8;
      p.gravity= cfg.gravity || 0;
      p.shape  = cfg.shape || "rect";
      p.active = true;
      this._active.push(p);
    }
  }

  // Preset effects
  hitEffect(x,y)     { this.burst(x,y,{color:["#ff4400","#ff8800","#fff"],count:10,speed:120,size:3,life:0.4}); }
  deathEffect(x,y)   { this.burst(x,y,{color:["#cc0000","#660000","#ff4400"],count:20,speed:90,size:5,life:1.0}); }
  levelUpEffect(x,y) { this.burst(x,y,{color:["#ffd700","#fff","#00ff88"],count:28,speed:100,size:4,life:1.3,gravity:-60}); }
  magicEffect(x,y)   { this.burst(x,y,{color:["#9944ff","#4488ff","#fff"],count:16,speed:70,size:3,life:0.9}); }
  healEffect(x,y)    { this.burst(x,y,{color:["#00ff88","#00cc44","#fff"],count:12,speed:45,size:4,life:1.0,gravity:-40}); }
  sparkEffect(x,y)   { this.burst(x,y,{color:["#ffffaa","#ffff00","#fff"],count:8,speed:60,size:2,life:0.5}); }
  bloodEffect(x,y)   { this.burst(x,y,{color:["#cc0000","#880000"],count:15,speed:80,size:3,life:0.7,gravity:120}); }

  // ─────────────────────────────────────────────
  // EMITTER (continuous)
  // ─────────────────────────────────────────────
  createEmitter(x, y, cfg={}) {
    const em = {
      x, y, active:true,
      rate:    cfg.rate    || 5,
      color:   cfg.color   || "#fff",
      speed:   cfg.speed   || 50,
      life:    cfg.life    || 0.5,
      size:    cfg.size    || 3,
      gravity: cfg.gravity || 0,
      spread:  cfg.spread  || 10,
      _timer:  0,
    };
    this._emitters.push(em);
    return em;
  }

  // ─────────────────────────────────────────────
  // UPDATE / RENDER
  // ─────────────────────────────────────────────
  update(dt) {
    const scaledDt = this.engine.time ? this.engine.time.scale(dt) : dt;

    // Emitters
    this._emitters = this._emitters.filter(em => {
      if (!em.active) return false;
      em._timer += scaledDt;
      const interval = 1 / em.rate;
      while (em._timer >= interval) {
        em._timer -= interval;
        this.burst(em.x, em.y, { count:1, color:em.color, speed:em.speed, life:em.life, size:em.size, gravity:em.gravity, spread:em.spread });
      }
      return true;
    });

    // Particles
    const dead = [];
    this._active = this._active.filter(p => {
      p.x    += p.vx * scaledDt;
      p.y    += p.vy * scaledDt;
      p.vy   += p.gravity * scaledDt;
      p.vx   *= 0.98;
      p.life -= scaledDt;
      if (p.life <= 0) { dead.push(p); return false; }
      return true;
    });
    dead.forEach(p => this._release(p));
  }

  render(ctx) {
    this._active.forEach(p => {
      ctx.globalAlpha = Math.max(0, p.life / p.maxLife);
      ctx.fillStyle   = p.color;
      if (p.shape === "circle") {
        ctx.beginPath(); ctx.arc(p.x, p.y, p.size/2, 0, Math.PI*2); ctx.fill();
      } else {
        ctx.fillRect(p.x - p.size/2, p.y - p.size/2, p.size, p.size);
      }
    });
    ctx.globalAlpha = 1;
  }
}

// ═══════════════════════════════════════════════════════════
// API BRIDGE — live world data from Discord bot
// ═══════════════════════════════════════════════════════════
class APIBridge {
  constructor(engine) {
    this.engine     = engine;
    this.base       = engine.config.apiBase || "http://45.59.113.113:8000";
    this.worldData  = null;
    this.characters = [];
    this.monsters   = [];
    this.laws       = [];
    this._timer     = 0;
    this._interval  = 30;
    this._fetching  = false;
    this._retries   = 0;
    this._maxRetries= 3;
  }

  async fetchAll() {
    if (this._fetching) return;
    this._fetching = true;
    try {
      const [world, chars, monsters, laws] = await Promise.all([
        this._fetch("/world"),
        this._fetch("/characters"),
        this._fetch("/monsters?status=alive"),
        this._fetch("/laws"),
      ]);
      this.worldData  = world;
      this.characters = Array.isArray(chars)   ? chars   : [];
      this.monsters   = Array.isArray(monsters)? monsters: [];
      this.laws       = Array.isArray(laws)    ? laws    : [];
      this._retries   = 0;
      this.engine.emit("api:updated", {
        world: this.worldData,
        chars: this.characters,
        monsters: this.monsters,
        laws: this.laws,
      });
    } catch(e) {
      this._retries++;
      console.warn(`[API] Fetch failed (attempt ${this._retries}):`, e.message);
      this.engine.emit("api:error", { error:e.message, retries:this._retries });
      // Exponential backoff
      this._interval = Math.min(120, 30 * this._retries);
    }
    this._fetching = false;
  }

  async _fetch(path) {
    const res = await fetch(this.base + path, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  update(dt) {
    this._timer += dt;
    if (this._timer >= this._interval) {
      this._timer = 0;
      this.fetchAll();
    }
  }
}
