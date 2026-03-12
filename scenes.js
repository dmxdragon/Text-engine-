/**
 * Nixon RPG Engine — Scene Manager
 * Handles scene transitions, stacking, lifecycle
 */

class SceneManager {
  constructor(engine) {
    this.engine  = engine;
    this._scenes = {};        // name → Scene class
    this._stack  = [];        // active scene stack
    this._transition = null;  // current transition
  }

  // Register a scene class
  register(name, SceneClass) {
    this._scenes[name] = SceneClass;
    return this;
  }

  // Start a scene (replaces current)
  async start(name, data = {}) {
    const SceneClass = this._scenes[name];
    if (!SceneClass) { console.error(`Scene not found: ${name}`); return; }

    // Fade out current
    if (this._stack.length > 0) {
      await this._fadeOut();
      const current = this._stack[this._stack.length - 1];
      current.onSleep?.();
    }

    const scene = new SceneClass(this.engine, data);
    this._stack = [scene];
    await scene.preload?.();
    await scene.create?.();
    await this._fadeIn();

    this.engine.emit("scene:started", { name });
    console.log(`[Scene] Started: ${name}`);
  }

  // Push a scene on top (overlay)
  async push(name, data = {}) {
    const SceneClass = this._scenes[name];
    if (!SceneClass) return;

    const current = this._stack[this._stack.length - 1];
    current?.onSleep?.();

    const scene = new SceneClass(this.engine, data);
    this._stack.push(scene);
    await scene.preload?.();
    await scene.create?.();

    this.engine.emit("scene:pushed", { name });
  }

  // Pop top scene
  async pop() {
    if (this._stack.length <= 1) return;
    const top = this._stack.pop();
    top.onDestroy?.();

    const current = this._stack[this._stack.length - 1];
    current?.onWake?.();

    this.engine.emit("scene:popped");
  }

  // Update all active scenes
  update(dt) {
    if (this._transition) this._transition.update(dt);
    for (const scene of this._stack) {
      scene.update?.(dt);
    }
  }

  // Render all active scenes (bottom to top)
  render(ctx) {
    for (const scene of this._stack) {
      scene.render?.(ctx);
    }
    if (this._transition) this._transition.render(ctx);
  }

  // Render UI (top scene only)
  renderUI(ctx) {
    const top = this._stack[this._stack.length - 1];
    top?.renderUI?.(ctx);
  }

  get current() {
    return this._stack[this._stack.length - 1] || null;
  }

  // ─────────────────────────────────────────────
  // TRANSITIONS
  // ─────────────────────────────────────────────
  _fadeOut(duration = 0.4) {
    return new Promise(resolve => {
      this._transition = new FadeTransition(this.engine, "out", duration, resolve);
    });
  }

  _fadeIn(duration = 0.4) {
    return new Promise(resolve => {
      this._transition = new FadeTransition(this.engine, "in", duration, () => {
        this._transition = null;
        resolve();
      });
    });
  }

  // Chapter transition — dramatic cinematic
  async chapterTransition(chapterData) {
    return new Promise(resolve => {
      this._transition = new ChapterTransition(this.engine, chapterData, resolve);
    });
  }
}

// ─────────────────────────────────────────────
// BASE SCENE
// ─────────────────────────────────────────────
class Scene {
  constructor(engine, data = {}) {
    this.engine = engine;
    this.data   = data;
    this.entities = [];
  }

  // Lifecycle hooks (override in subclass)
  async preload()  {}
  async create()   {}
  update(dt)       {}
  render(ctx)      {}
  renderUI(ctx)    {}
  onSleep()        {}
  onWake()         {}
  onDestroy()      {}

  // Entity management
  add(entity) {
    this.entities.push(entity);
    entity.scene = this;
    return entity;
  }

  remove(entity) {
    this.entities = this.entities.filter(e => e !== entity);
  }

  updateEntities(dt) {
    this.entities.forEach(e => e.update?.(dt));
    // Remove dead entities
    this.entities = this.entities.filter(e => !e.dead);
  }

  renderEntities(ctx) {
    // Sort by Y for depth (painter's algorithm)
    const sorted = [...this.entities].sort((a, b) => (a.y + (a.h||0)) - (b.y + (b.h||0)));
    sorted.forEach(e => e.render?.(ctx));
  }
}

// ─────────────────────────────────────────────
// FADE TRANSITION
// ─────────────────────────────────────────────
class FadeTransition {
  constructor(engine, direction, duration, onComplete) {
    this.engine     = engine;
    this.direction  = direction; // "in" | "out"
    this.duration   = duration;
    this.onComplete = onComplete;
    this.elapsed    = 0;
    this.done       = false;
  }

  update(dt) {
    if (this.done) return;
    this.elapsed += dt;
    if (this.elapsed >= this.duration) {
      this.done = true;
      this.onComplete?.();
    }
  }

  render(ctx) {
    if (this.done) return;
    const t = Math.min(this.elapsed / this.duration, 1);
    const alpha = this.direction === "out" ? t : 1 - t;
    ctx.fillStyle = `rgba(0,0,0,${alpha})`;
    ctx.fillRect(0, 0, this.engine.canvas.width, this.engine.canvas.height);
  }
}

// ─────────────────────────────────────────────
// CHAPTER TRANSITION — CINEMATIC
// ─────────────────────────────────────────────
class ChapterTransition {
  constructor(engine, data, onComplete) {
    this.engine     = engine;
    this.data       = data;
    this.onComplete = onComplete;
    this.elapsed    = 0;
    this.duration   = 5.0; // 5 seconds
    this.phase      = "fade_out"; // fade_out → show_title → show_narrative → fade_in
    this.done       = false;

    this.particles  = [];
    this._spawnParticles();
  }

  _spawnParticles() {
    const W = this.engine.canvas.width;
    const H = this.engine.canvas.height;
    for (let i = 0; i < 80; i++) {
      this.particles.push({
        x: Math.random() * W,
        y: Math.random() * H,
        vx: (Math.random() - 0.5) * 1.5,
        vy: -Math.random() * 2 - 0.5,
        size: Math.random() * 4 + 1,
        alpha: Math.random(),
        color: ["#ffd700","#ff8800","#ffffff","#9944ff"][Math.floor(Math.random()*4)],
      });
    }
  }

  update(dt) {
    if (this.done) return;
    this.elapsed += dt;

    // Update particles
    this.particles.forEach(p => {
      p.x += p.vx; p.y += p.vy;
      p.alpha -= 0.005;
      if (p.alpha <= 0 || p.y < 0) {
        p.y = this.engine.canvas.height + 10;
        p.x = Math.random() * this.engine.canvas.width;
        p.alpha = Math.random();
      }
    });

    if (this.elapsed >= this.duration) {
      this.done = true;
      this.onComplete?.();
    }
  }

  render(ctx) {
    if (this.done) return;
    const W = this.engine.canvas.width;
    const H = this.engine.canvas.height;
    const t = this.elapsed / this.duration;

    // Dark overlay
    const alpha = t < 0.2 ? t/0.2 : t > 0.8 ? 1-(t-0.8)/0.2 : 1;
    ctx.fillStyle = `rgba(0,0,0,${alpha * 0.92})`;
    ctx.fillRect(0, 0, W, H);

    // Particles
    this.particles.forEach(p => {
      ctx.fillStyle = p.color;
      ctx.globalAlpha = Math.max(0, p.alpha) * alpha;
      ctx.fillRect(p.x, p.y, p.size, p.size);
    });
    ctx.globalAlpha = 1;

    if (t < 0.15 || t > 0.85) return;

    const textAlpha = t < 0.3 ? (t-0.15)/0.15 : t > 0.7 ? 1-(t-0.7)/0.15 : 1;
    ctx.globalAlpha = textAlpha;

    // Chapter name
    ctx.fillStyle = "#ffd700";
    ctx.font = `bold ${Math.floor(W * 0.045)}px 'Press Start 2P', monospace`;
    ctx.textAlign = "center";
    ctx.fillText(this.data.chapter_name || "New Chapter", W/2, H/2 - 60);

    // Narrative
    ctx.fillStyle = "#cccccc";
    ctx.font = `${Math.floor(W * 0.018)}px 'VT323', monospace`;
    const narrative = (this.data.narrative || "").slice(0, 120) + "...";
    this._wrapText(ctx, narrative, W/2, H/2, W * 0.7, 28);

    // Boss warning
    if (this.data.new_boss) {
      ctx.fillStyle = "#cc0000";
      ctx.font = `${Math.floor(W * 0.022)}px 'VT323', monospace`;
      ctx.fillText(`⚠ ${this.data.new_boss.name} awakens...`, W/2, H/2 + 100);
    }

    // Dark age warning
    if (this.data.is_dark_age) {
      ctx.fillStyle = "#660000";
      ctx.font = `${Math.floor(W * 0.02)}px 'VT323', monospace`;
      ctx.fillText("🌑 DARK AGE DESCENDS", W/2, H/2 + 140);
    }

    ctx.globalAlpha = 1;
    ctx.textAlign = "left";
  }

  _wrapText(ctx, text, x, y, maxWidth, lineH) {
    const words = text.split(" ");
    let line = "";
    let lineY = y;
    for (const word of words) {
      const test = line + word + " ";
      if (ctx.measureText(test).width > maxWidth && line) {
        ctx.fillText(line, x, lineY);
        line  = word + " ";
        lineY += lineH;
      } else {
        line = test;
      }
    }
    ctx.fillText(line, x, lineY);
  }
}
