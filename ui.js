/**
 * Nixon RPG Engine — UI, Save/Load, Debug, Effects, Time
 */

// ═══════════════════════════════════════════════════════════
// UI SYSTEM
// ═══════════════════════════════════════════════════════════
class UISystem {
  constructor(engine) {
    this.engine   = engine;
    this._elements= [];
    this._stack   = []; // modal stack
  }

  add(element) {
    element.engine = this.engine;
    element.ui     = this;
    this._elements.push(element);
    element.init?.();
    return element;
  }

  remove(element) {
    this._elements = this._elements.filter(e => e !== element);
  }

  clear() {
    this._elements.forEach(e => e.destroy?.());
    this._elements = [];
  }

  update(dt) {
    this._elements.forEach(e => { if (e.active !== false) e.update?.(dt); });
  }

  render(ctx) {
    // Sort by z
    const sorted = [...this._elements].sort((a,b) => (a.z||0)-(b.z||0));
    sorted.forEach(e => { if (e.visible !== false) e.render?.(ctx); });

    // Render input manager UI (virtual joystick)
    this.engine.input?.render(ctx);
  }

  // ── Factories ──
  text(x, y, text, opts={}) {
    return this.add(new UIText(x, y, text, opts));
  }

  button(x, y, w, h, label, onClick, opts={}) {
    return this.add(new UIButton(x, y, w, h, label, onClick, opts));
  }

  hpBar(x, y, w, h, getter, opts={}) {
    return this.add(new UIBar(x, y, w, h, getter, { color:"#cc2200", bg:"#330000", ...opts }));
  }

  panel(x, y, w, h, opts={}) {
    return this.add(new UIPanel(x, y, w, h, opts));
  }

  dialog(title, message, buttons=[]) {
    return this.add(new UIDialog(title, message, buttons));
  }
}

// ─────────────────────────────────────────────
// UI ELEMENT BASE
// ─────────────────────────────────────────────
class UIElement {
  constructor(x, y) {
    this.x       = x;
    this.y       = y;
    this.visible = true;
    this.active  = true;
    this.z       = 0;
    this.alpha   = 1;
    this.engine  = null;
    this.ui      = null;
  }
  init()      {}
  update(dt)  {}
  render(ctx) {}
  destroy()   { this.ui?.remove(this); }
}

// ─────────────────────────────────────────────
// UI TEXT
// ─────────────────────────────────────────────
class UIText extends UIElement {
  constructor(x, y, text, opts={}) {
    super(x, y);
    this.text    = text;
    this.font    = opts.font    || "16px 'VT323', monospace";
    this.color   = opts.color   || "#ffffff";
    this.align   = opts.align   || "left";
    this.shadow  = opts.shadow  || false;
    this.outline = opts.outline || false;
  }

  render(ctx) {
    ctx.globalAlpha = this.alpha;
    ctx.font        = this.font;
    ctx.textAlign   = this.align;

    if (this.shadow) {
      ctx.fillStyle = "rgba(0,0,0,0.7)";
      ctx.fillText(this.text, this.x+2, this.y+2);
    }
    if (this.outline) {
      ctx.strokeStyle = "#000";
      ctx.lineWidth   = 3;
      ctx.strokeText(this.text, this.x, this.y);
    }
    ctx.fillStyle = this.color;
    ctx.fillText(this.text, this.x, this.y);
    ctx.textAlign   = "left";
    ctx.globalAlpha = 1;
  }
}

// ─────────────────────────────────────────────
// UI BUTTON
// ─────────────────────────────────────────────
class UIButton extends UIElement {
  constructor(x, y, w, h, label, onClick, opts={}) {
    super(x, y);
    this.w       = w;
    this.h       = h;
    this.label   = label;
    this.onClick = onClick;
    this.color   = opts.color   || "#1a1a2e";
    this.hover   = opts.hover   || "#2a2a4e";
    this.border  = opts.border  || "#ffd700";
    this.textColor=opts.textColor||"#ffffff";
    this.font    = opts.font    || "12px 'VT323', monospace";
    this._hovered= false;
    this._pressed= false;
  }

  init() {
    this.engine.on("input:mousedown", e => {
      if (this._hitTest(e.x, e.y)) {
        this._pressed = true;
        this.engine.audio.play("click");
      }
    });
    this.engine.on("input:mouseup", () => {
      if (this._pressed && this._hovered) {
        this.onClick?.();
        this.engine.particles.burst(this.x + this.w/2, this.y + this.h/2, { count:6, color:"#ffd700", speed:40 });
      }
      this._pressed = false;
    });
    this.engine.on("input:click", e => {
      if (this._hitTest(e.x, e.y)) this.onClick?.();
    });
  }

  update(dt) {
    const m = this.engine.input.mouse;
    this._hovered = this._hitTest(m.x, m.y);
  }

  _hitTest(mx, my) {
    return mx >= this.x && mx <= this.x+this.w &&
           my >= this.y && my <= this.y+this.h;
  }

  render(ctx) {
    ctx.globalAlpha = this.alpha;
    ctx.fillStyle   = this._pressed ? this.border : (this._hovered ? this.hover : this.color);
    ctx.fillRect(this.x, this.y, this.w, this.h);
    ctx.strokeStyle = this.border;
    ctx.lineWidth   = this._hovered ? 2 : 1;
    ctx.strokeRect(this.x, this.y, this.w, this.h);

    ctx.fillStyle   = this._pressed ? "#000" : this.textColor;
    ctx.font        = this.font;
    ctx.textAlign   = "center";
    ctx.fillText(this.label, this.x + this.w/2, this.y + this.h/2 + 5);
    ctx.textAlign   = "left";
    ctx.globalAlpha = 1;
  }
}

// ─────────────────────────────────────────────
// UI BAR (HP, MP, XP, etc.)
// ─────────────────────────────────────────────
class UIBar extends UIElement {
  constructor(x, y, w, h, getter, opts={}) {
    super(x, y);
    this.w      = w;
    this.h      = h;
    this.getter = getter; // () => { current, max }
    this.color  = opts.color  || "#cc2200";
    this.bg     = opts.bg     || "#330000";
    this.label  = opts.label  || null;
    this._display = 1; // smoothed display value
  }

  update(dt) {
    const { current, max } = this.getter();
    const target = max > 0 ? current/max : 0;
    this._display += (target - this._display) * Math.min(dt * 8, 1);
  }

  render(ctx) {
    ctx.globalAlpha = this.alpha;
    // Background
    ctx.fillStyle = this.bg;
    ctx.fillRect(this.x, this.y, this.w, this.h);
    // Fill
    ctx.fillStyle = this.color;
    ctx.fillRect(this.x, this.y, this.w * Math.max(0, this._display), this.h);
    // Border
    ctx.strokeStyle = "#000";
    ctx.lineWidth   = 1;
    ctx.strokeRect(this.x, this.y, this.w, this.h);
    // Label
    if (this.label) {
      ctx.fillStyle = "#fff";
      ctx.font      = "10px 'VT323', monospace";
      ctx.textAlign = "center";
      ctx.fillText(this.label, this.x + this.w/2, this.y + this.h - 1);
      ctx.textAlign = "left";
    }
    ctx.globalAlpha = 1;
  }
}

// ─────────────────────────────────────────────
// UI PANEL
// ─────────────────────────────────────────────
class UIPanel extends UIElement {
  constructor(x, y, w, h, opts={}) {
    super(x, y);
    this.w          = w;
    this.h          = h;
    this.bg         = opts.bg      || "rgba(10,10,20,0.95)";
    this.border     = opts.border  || "#333366";
    this.title      = opts.title   || null;
    this.children   = [];
  }

  add(element) {
    this.children.push(element);
    return element;
  }

  render(ctx) {
    ctx.globalAlpha = this.alpha;
    ctx.fillStyle   = this.bg;
    ctx.fillRect(this.x, this.y, this.w, this.h);
    ctx.strokeStyle = this.border;
    ctx.lineWidth   = 1;
    ctx.strokeRect(this.x, this.y, this.w, this.h);

    if (this.title) {
      ctx.fillStyle = this.border;
      ctx.fillRect(this.x, this.y, this.w, 22);
      ctx.fillStyle = "#fff";
      ctx.font      = "8px 'Press Start 2P', monospace";
      ctx.textAlign = "center";
      ctx.fillText(this.title, this.x + this.w/2, this.y + 14);
      ctx.textAlign = "left";
    }
    this.children.forEach(c => c.render?.(ctx));
    ctx.globalAlpha = 1;
  }

  update(dt) {
    this.children.forEach(c => c.update?.(dt));
  }
}

// ─────────────────────────────────────────────
// UI DIALOG (modal)
// ─────────────────────────────────────────────
class UIDialog extends UIElement {
  constructor(title, message, buttons=[]) {
    const W = 400, H = 180;
    super(0, 0); // centered dynamically
    this.title   = title;
    this.message = message;
    this.buttons = buttons; // [{ label, onClick, color }]
    this.w = W; this.h = H;
    this.z = 100;
  }

  init() {
    this.x = (this.engine.canvas.width  - this.w) / 2;
    this.y = (this.engine.canvas.height - this.h) / 2;
  }

  render(ctx) {
    const W = this.engine.canvas.width;
    const H = this.engine.canvas.height;
    // Backdrop
    ctx.fillStyle = "rgba(0,0,0,0.7)";
    ctx.fillRect(0, 0, W, H);

    // Panel
    ctx.fillStyle = "#0a0a1a";
    ctx.fillRect(this.x, this.y, this.w, this.h);
    ctx.strokeStyle = "#ffd700";
    ctx.lineWidth   = 2;
    ctx.strokeRect(this.x, this.y, this.w, this.h);

    // Title
    ctx.fillStyle = "#ffd700";
    ctx.font      = "9px 'Press Start 2P', monospace";
    ctx.textAlign = "center";
    ctx.fillText(this.title, this.x + this.w/2, this.y + 24);

    // Message
    ctx.fillStyle = "#cccccc";
    ctx.font      = "16px 'VT323', monospace";
    ctx.fillText(this.message, this.x + this.w/2, this.y + 55);

    // Buttons
    const bw = 100, bh = 28, gap = 16;
    const total = this.buttons.length * bw + (this.buttons.length-1) * gap;
    let bx = this.x + (this.w - total) / 2;
    const by = this.y + this.h - 44;

    this.buttons.forEach(btn => {
      ctx.fillStyle = btn.color || "#1a1a2e";
      ctx.fillRect(bx, by, bw, bh);
      ctx.strokeStyle = "#555";
      ctx.lineWidth   = 1;
      ctx.strokeRect(bx, by, bw, bh);
      ctx.fillStyle = "#fff";
      ctx.font      = "13px 'VT323', monospace";
      ctx.fillText(btn.label, bx + bw/2, by + bh/2 + 5);
      bx += bw + gap;
    });

    ctx.textAlign = "left";
  }

  update(dt) {
    const m = this.engine.input?.mouse;
    if (!m) return;

    const bw = 100, bh = 28, gap = 16;
    const total = this.buttons.length * bw + (this.buttons.length-1) * gap;
    let bx = this.x + (this.w - total) / 2;
    const by = this.y + this.h - 44;

    if (this.engine.input.mouseJustPressed(0)) {
      this.buttons.forEach(btn => {
        if (m.x >= bx && m.x <= bx+bw && m.y >= by && m.y <= by+bh) {
          btn.onClick?.();
          this.destroy();
        }
        bx += bw + gap;
      });
    }
  }
}

// ═══════════════════════════════════════════════════════════
// SAVE / LOAD SYSTEM
// ═══════════════════════════════════════════════════════════
class SaveSystem {
  constructor(engine) {
    this.engine  = engine;
    this.slotKey = "nixonrpg_save";
  }

  save(slotId = 0, data = {}) {
    try {
      const saveData = {
        version:   1,
        timestamp: Date.now(),
        slot:      slotId,
        ...data,
      };
      localStorage.setItem(`${this.slotKey}_${slotId}`, JSON.stringify(saveData));
      this.engine.emit("save:saved", { slot:slotId });
      console.log(`[Save] Slot ${slotId} saved`);
      return true;
    } catch(e) {
      console.error("[Save] Failed:", e);
      return false;
    }
  }

  load(slotId = 0) {
    try {
      const raw = localStorage.getItem(`${this.slotKey}_${slotId}`);
      if (!raw) return null;
      const data = JSON.parse(raw);
      this.engine.emit("save:loaded", { slot:slotId, data });
      console.log(`[Save] Slot ${slotId} loaded`);
      return data;
    } catch(e) {
      console.error("[Save] Load failed:", e);
      return null;
    }
  }

  delete(slotId = 0) {
    localStorage.removeItem(`${this.slotKey}_${slotId}`);
    this.engine.emit("save:deleted", { slot:slotId });
  }

  exists(slotId = 0) {
    return !!localStorage.getItem(`${this.slotKey}_${slotId}`);
  }

  // Download save as JSON file
  download(slotId = 0) {
    const data = this.load(slotId);
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type:"application/json" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `nixonrpg_save_${slotId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // Upload from file
  upload(onLoad) {
    const input = document.createElement("input");
    input.type  = "file";
    input.accept= ".json";
    input.onchange = e => {
      const file = e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = ev => {
        try {
          const data = JSON.parse(ev.target.result);
          onLoad?.(data);
        } catch(err) {
          console.error("[Save] Upload parse error:", err);
        }
      };
      reader.readAsText(file);
    };
    input.click();
  }
}

// ═══════════════════════════════════════════════════════════
// SCREEN EFFECTS
// ═══════════════════════════════════════════════════════════
class ScreenEffects {
  constructor(engine) {
    this.engine = engine;
    this._effects = [];
  }

  // Flash (damage flash, pickup flash etc)
  flash(color = "#ffffff", duration = 0.15, intensity = 0.6) {
    this._effects.push({ type:"flash", color, duration, elapsed:0, intensity });
  }

  // Vignette (permanent dark edges)
  vignette(intensity = 0.5) {
    this._vignetteIntensity = intensity;
  }

  // Tint entire screen
  tint(color, alpha, duration) {
    this._effects.push({ type:"tint", color, alpha, duration, elapsed:0 });
  }

  // Fade to color
  fade(color, alpha, duration, onDone) {
    this._effects.push({ type:"fade", color, alpha, duration, elapsed:0, onDone });
  }

  update(dt) {
    this._effects = this._effects.filter(e => {
      e.elapsed += dt;
      if (e.elapsed >= e.duration) {
        e.onDone?.();
        return false;
      }
      return true;
    });
  }

  render(ctx) {
    const W = this.engine.canvas.width;
    const H = this.engine.canvas.height;

    // Vignette
    if (this._vignetteIntensity > 0) {
      const grad = ctx.createRadialGradient(W/2, H/2, H*0.3, W/2, H/2, H*0.8);
      grad.addColorStop(0, "rgba(0,0,0,0)");
      grad.addColorStop(1, `rgba(0,0,0,${this._vignetteIntensity})`);
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, W, H);
    }

    // Effects
    this._effects.forEach(e => {
      const t = e.elapsed / e.duration;
      if (e.type === "flash") {
        const a = e.intensity * (1 - t);
        ctx.fillStyle = e.color;
        ctx.globalAlpha = a;
        ctx.fillRect(0, 0, W, H);
        ctx.globalAlpha = 1;
      }
      if (e.type === "tint") {
        ctx.fillStyle   = e.color;
        ctx.globalAlpha = e.alpha * (1 - t);
        ctx.fillRect(0, 0, W, H);
        ctx.globalAlpha = 1;
      }
      if (e.type === "fade") {
        const a = t < 0.5 ? t/0.5 * e.alpha : e.alpha * (1-(t-0.5)/0.5);
        ctx.fillStyle   = e.color;
        ctx.globalAlpha = a;
        ctx.fillRect(0, 0, W, H);
        ctx.globalAlpha = 1;
      }
    });
  }
}

// ═══════════════════════════════════════════════════════════
// TIME MANAGER
// ═══════════════════════════════════════════════════════════
class TimeManager {
  constructor(engine) {
    this.engine     = engine;
    this.timeScale  = 1.0;   // 0=pause, 0.5=slow, 1=normal, 2=fast
    this.fixedStep  = 1/60;  // fixed physics timestep
    this._accumulator = 0;

    // Timers
    this._timers = [];
    // Tweens
    this._tweens = [];
  }

  // Scale delta time
  scale(dt) {
    return dt * this.timeScale;
  }

  pause()   { this._prevScale = this.timeScale; this.timeScale = 0; }
  resume()  { this.timeScale  = this._prevScale || 1; }
  slowMo(factor = 0.3, duration = 1.0) {
    this.timeScale = factor;
    this.after(duration, () => { this.timeScale = 1; });
  }

  // One-shot timer
  after(seconds, callback) {
    this._timers.push({ elapsed:0, duration:seconds, callback, repeat:false });
  }

  // Repeating timer
  every(seconds, callback, times = Infinity) {
    this._timers.push({ elapsed:0, duration:seconds, callback, repeat:true, times, count:0 });
  }

  // Tween a property
  tween(target, prop, from, to, duration, easing = "linear", onDone = null) {
    this._tweens.push({ target, prop, from, to, duration, elapsed:0, easing, onDone });
    target[prop] = from;
    return this;
  }

  update(dt) {
    const scaledDt = this.scale(dt);

    // Timers
    this._timers = this._timers.filter(t => {
      t.elapsed += scaledDt;
      if (t.elapsed >= t.duration) {
        t.elapsed = 0;
        t.callback();
        if (t.repeat) {
          t.count++;
          return t.count < t.times;
        }
        return false;
      }
      return true;
    });

    // Tweens
    this._tweens = this._tweens.filter(tw => {
      tw.elapsed += scaledDt;
      const t = Math.min(tw.elapsed / tw.duration, 1);
      const e = this._ease(tw.easing, t);
      tw.target[tw.prop] = tw.from + (tw.to - tw.from) * e;
      if (t >= 1) {
        tw.onDone?.();
        return false;
      }
      return true;
    });
  }

  _ease(type, t) {
    switch(type) {
      case "linear":    return t;
      case "easeIn":    return t*t;
      case "easeOut":   return 1-(1-t)*(1-t);
      case "easeInOut": return t<0.5 ? 2*t*t : 1-2*(1-t)*(1-t);
      case "bounce":    return t<0.5 ? 4*t*t*t : 1+4*(t-1)*(t-1)*(t-1);
      case "elastic":   return t===0||t===1 ? t : Math.pow(2,-10*t)*Math.sin((t*10-0.75)*(2*Math.PI/3))+1;
      default: return t;
    }
  }
}

// ═══════════════════════════════════════════════════════════
// DEBUG TOOLS
// ═══════════════════════════════════════════════════════════
class DebugTools {
  constructor(engine) {
    this.engine   = engine;
    this.enabled  = false;
    this._logs    = [];
    this._commands= {};
    this._consoleOpen = false;
    this._consoleInput= "";

    // Register built-in commands
    this.register("fps",    () => `FPS: ${engine.fps}`);
    this.register("zoom",   (v) => { if(v) engine.camera.setZoom(parseFloat(v)); return `zoom: ${engine.camera.zoom.toFixed(2)}`; });
    this.register("shake",  () => { engine.camera.shake(20, 0.5); return "shake!"; });
    this.register("help",   () => Object.keys(this._commands).join(", "));
    this.register("time",   (v) => { if(v) engine.time.timeScale=parseFloat(v); return `timeScale: ${engine.time.timeScale}`; });
    this.register("flash",  () => { engine.effects.flash("#ff0000", 0.3); return "flash!"; });

    // Toggle with F1
    engine.on("input:keydown", ({ code }) => {
      if (code === "F1") { this.enabled = !this.enabled; }
      if (code === "F2") { this._consoleOpen = !this._consoleOpen; }
    });
  }

  register(name, handler) {
    this._commands[name] = handler;
  }

  log(msg) {
    this._logs.push({ msg: String(msg), time: Date.now() });
    if (this._logs.length > 20) this._logs.shift();
  }

  exec(cmd) {
    const parts = cmd.trim().split(" ");
    const name  = parts[0];
    const args  = parts.slice(1);
    const handler = this._commands[name];
    if (handler) {
      const result = handler(...args);
      if (result) this.log(`> ${result}`);
    } else {
      this.log(`Unknown command: ${name}`);
    }
  }

  update(dt) {}

  render(ctx) {
    if (!this.enabled) return;
    const W = this.engine.canvas.width;
    const H = this.engine.canvas.height;

    // FPS
    ctx.fillStyle   = "rgba(0,0,0,0.6)";
    ctx.fillRect(0, 0, 180, 80);
    ctx.fillStyle   = "#00ff00";
    ctx.font        = "11px monospace";
    ctx.fillText(`FPS:    ${this.engine.fps}`,                     8, 16);
    ctx.fillText(`dt:     ${(this.engine.deltaTime*1000).toFixed(1)}ms`, 8, 30);
    ctx.fillText(`cam:    ${Math.round(this.engine.camera.x)},${Math.round(this.engine.camera.y)}`, 8, 44);
    ctx.fillText(`zoom:   ${this.engine.camera.zoom.toFixed(2)}`, 8, 58);
    ctx.fillText(`time:   ${this.engine.time?.timeScale.toFixed(2)||1}x`, 8, 72);

    // Scene info
    const scene = this.engine.scenes.current;
    if (scene) {
      ctx.fillText(`scene:  ${scene.constructor.name}`, 8, 86);
      ctx.fillText(`entities: ${scene.entities?.length||0}`, 8, 100);
    }

    // Collision overlay
    if (scene?.tilemap) {
      const cam = this.engine.camera;
      const ts  = scene.tilemap.tileSize;
      const startX = Math.max(0, Math.floor(cam.x / ts));
      const startY = Math.max(0, Math.floor(cam.y / ts));
      const endX   = Math.min(scene.tilemap.width,  startX + Math.ceil(W / (ts*cam.zoom)) + 2);
      const endY   = Math.min(scene.tilemap.height, startY + Math.ceil(H / (ts*cam.zoom)) + 2);

      ctx.save();
      cam.applyTransform(ctx);
      for (let y=startY; y<endY; y++) {
        for (let x=startX; x<endX; x++) {
          if (scene.tilemap.isSolid(x,y)) {
            ctx.fillStyle = "rgba(255,0,0,0.2)";
            ctx.fillRect(x*ts, y*ts, ts, ts);
            ctx.strokeStyle = "rgba(255,0,0,0.4)";
            ctx.lineWidth   = 0.5;
            ctx.strokeRect(x*ts, y*ts, ts, ts);
          }
        }
      }
      // Grid
      ctx.strokeStyle = "rgba(255,255,255,0.05)";
      ctx.lineWidth   = 0.5;
      for (let x=startX; x<endX; x++) { ctx.beginPath(); ctx.moveTo(x*ts,startY*ts); ctx.lineTo(x*ts,endY*ts); ctx.stroke(); }
      for (let y=startY; y<endY; y++) { ctx.beginPath(); ctx.moveTo(startX*ts,y*ts); ctx.lineTo(endX*ts,y*ts); ctx.stroke(); }
      ctx.restore();
    }

    // Log
    if (this._logs.length > 0) {
      const logH = this._logs.length * 16 + 8;
      ctx.fillStyle = "rgba(0,0,0,0.7)";
      ctx.fillRect(W-280, H-logH-8, 272, logH);
      this._logs.forEach((l, i) => {
        ctx.fillStyle = "#aaffaa";
        ctx.font      = "11px monospace";
        ctx.fillText(l.msg.slice(0,36), W-276, H-logH+i*16);
      });
    }

    // F1 hint
    ctx.fillStyle = "#555";
    ctx.font      = "9px monospace";
    ctx.fillText("F1: debug  F2: console", 8, H-6);
  }
}

// ═══════════════════════════════════════════════════════════
// FULLSCREEN / RESPONSIVE
// ═══════════════════════════════════════════════════════════
class ResponsiveManager {
  constructor(engine) {
    this.engine = engine;
    this._setupResize();
    this._setupFullscreen();
  }

  _setupResize() {
    const resize = () => {
      const scale = Math.min(
        window.innerWidth  / this.engine.config.width,
        window.innerHeight / this.engine.config.height
      );
      const canvas = this.engine.canvas;
      canvas.style.width  = (this.engine.config.width  * scale) + "px";
      canvas.style.height = (this.engine.config.height * scale) + "px";
      canvas.style.position = "absolute";
      canvas.style.left = ((window.innerWidth  - this.engine.config.width  * scale) / 2) + "px";
      canvas.style.top  = ((window.innerHeight - this.engine.config.height * scale) / 2) + "px";
      this.engine.emit("engine:resize", { scale });
    };
    window.addEventListener("resize", resize);
    window.addEventListener("orientationchange", () => setTimeout(resize, 200));
    resize();
  }

  _setupFullscreen() {
    // Double-click / double-tap to toggle fullscreen
    this.engine.canvas.addEventListener("dblclick", () => this.toggleFullscreen());
  }

  toggleFullscreen() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen?.();
    } else {
      document.exitFullscreen?.();
    }
  }

  update(dt) {}
}
