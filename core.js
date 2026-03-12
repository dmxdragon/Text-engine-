/**
 * Nixon RPG Engine — Core v2
 * Wires all systems: loader, scenes, camera, input,
 * physics, particles, audio, UI, time, effects, debug, responsive
 */

class NixonEngine {
  constructor(config = {}) {
    this.config = {
      width:     config.width     || 1280,
      height:    config.height    || 720,
      tileSize:  config.tileSize  || 32,
      targetFPS: config.targetFPS || 60,
      apiBase:   config.apiBase   || "http://45.59.113.113:8000",
      canvas:    config.canvas    || "game-canvas",
      debug:     config.debug     || false,
    };

    this.canvas    = null;
    this.ctx       = null;
    this.running   = false;

    // All systems (assigned in init)
    this.loader    = null;
    this.scenes    = null;
    this.camera    = null;
    this.input     = null;
    this.audio     = null;
    this.particles = null;
    this.physics   = null;
    this.api       = null;
    this.ui        = null;
    this.time      = null;
    this.effects   = null;
    this.debug     = null;
    this.responsive= null;
    this.save      = null;

    // Timing
    this.lastTime  = 0;
    this.deltaTime = 0;
    this.fps       = 0;
    this._fpsCount = 0;
    this._fpsTimer = 0;

    // Event bus
    this._events   = {};

    // Chapter tracking
    this._lastChapter = null;
  }

  // ─────────────────────────────────────────────
  // INIT — create all systems in dependency order
  // ─────────────────────────────────────────────
  async init() {
    // 1. Canvas
    this.canvas = document.getElementById(this.config.canvas);
    this.canvas.width  = this.config.width;
    this.canvas.height = this.config.height;
    this.ctx = this.canvas.getContext("2d");
    this.ctx.imageSmoothingEnabled = false;

    // 2. Systems (order matters — some depend on others)
    this.time       = new TimeManager(this);
    this.audio      = new AudioEngine(this);
    this.loader     = new AssetLoader(this);
    this.camera     = new Camera(this);
    this.input      = new InputManager(this);
    this.physics    = new PhysicsEngine(this);
    this.particles  = new ParticleSystem(this);
    this.scenes     = new SceneManager(this);
    this.ui         = new UISystem(this);
    this.effects    = new ScreenEffects(this);
    this.debug      = new DebugTools(this);
    this.responsive = new ResponsiveManager(this);
    this.save       = new SaveSystem(this);
    this.api        = new APIBridge(this);

    // 3. Global shortcuts / keybinds
    this._setupGlobalKeys();

    console.log("⚔ Nixon Engine v2 initialized");
    console.log("   Systems: loader, scenes, camera, input, physics,");
    console.log("            particles, audio, ui, time, effects, debug,");
    console.log("            responsive, save, api");
    this.emit("engine:ready");
    return this;
  }

  _setupGlobalKeys() {
    // M = mute toggle
    this.on("input:keydown", ({ code }) => {
      if (code === "KeyM") {
        const muted = this.audio.toggleMute();
        this.debug.log(muted ? "Audio muted" : "Audio unmuted");
      }
      // +/- zoom
      if (code === "Equal" || code === "NumpadAdd")      this.camera.setZoom(this.camera.zoom + 0.2);
      if (code === "Minus" || code === "NumpadSubtract") this.camera.setZoom(this.camera.zoom - 0.2);
      // R = reset zoom
      if (code === "KeyR") { this.camera.setZoom(1.5); }
      // P = pause
      if (code === "KeyP") {
        if (this.time.timeScale === 0) this.time.resume();
        else this.time.pause();
        this.debug.log(this.time.timeScale === 0 ? "PAUSED" : "RESUMED");
      }
    });
  }

  // ─────────────────────────────────────────────
  // GAME LOOP
  // ─────────────────────────────────────────────
  start() {
    this.running  = true;
    this.lastTime = performance.now();
    requestAnimationFrame(t => this._loop(t));
    console.log("⚔ Game loop started");
  }

  stop() { this.running = false; }

  _loop(timestamp) {
    if (!this.running) return;

    // Raw delta (capped at 100ms)
    this.deltaTime = Math.min((timestamp - this.lastTime) / 1000, 0.1);
    this.lastTime  = timestamp;

    // FPS counter
    this._fpsCount++;
    this._fpsTimer += this.deltaTime;
    if (this._fpsTimer >= 1) {
      this.fps       = this._fpsCount;
      this._fpsCount = 0;
      this._fpsTimer = 0;
    }

    this._update(this.deltaTime);
    this._render();
    requestAnimationFrame(t => this._loop(t));
  }

  _update(dt) {
    this.time.update(dt);              // time scale / tweens / timers
    const sdt = this.time.scale(dt);   // scaled delta for game logic

    this.input.update(dt);             // input (raw dt for responsiveness)
    this.camera.update(dt);            // camera
    this.api.update(dt);               // api polling
    this.physics.update(dt);           // physics (has own fixed step)
    this.particles.update(dt);         // particles
    this.scenes.update(sdt);           // scenes + entities
    this.ui.update(sdt);               // ui elements
    this.effects.update(dt);           // screen effects
    this.debug.update(dt);             // debug tools
  }

  _render() {
    // Clear
    this.ctx.fillStyle = "#06060f";
    this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

    // World space (camera transform)
    this.ctx.save();
    this.camera.applyTransform(this.ctx);
    this.scenes.render(this.ctx);
    this.particles.render(this.ctx);
    this.ctx.restore();

    // Screen space (UI, effects, debug)
    this.scenes.renderUI(this.ctx);
    this.ui.render(this.ctx);
    this.effects.render(this.ctx);
    this.debug.render(this.ctx);
  }

  // ─────────────────────────────────────────────
  // EVENT BUS
  // ─────────────────────────────────────────────
  on(event, callback) {
    if (!this._events[event]) this._events[event] = [];
    this._events[event].push(callback);
    return () => this.off(event, callback); // returns unsubscribe fn
  }

  once(event, callback) {
    const wrapper = (data) => { callback(data); this.off(event, wrapper); };
    return this.on(event, wrapper);
  }

  off(event, callback) {
    if (!this._events[event]) return;
    this._events[event] = this._events[event].filter(cb => cb !== callback);
  }

  emit(event, data = {}) {
    if (!this._events[event]) return;
    this._events[event].forEach(cb => { try { cb(data); } catch(e) { console.error(`[Event:${event}]`, e); } });
  }
}
