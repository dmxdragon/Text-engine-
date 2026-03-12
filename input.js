/**
 * Nixon RPG Engine — Advanced Input Manager
 * Keyboard, Mouse, Touch با buffer و state tracking
 */

class InputManager {
  constructor(engine) {
    this.engine = engine;

    // ── Keyboard ──
    this._keys       = {};   // code → { down, justPressed, justReleased, repeat }
    this._keyBuffer  = [];   // ordered press history
    this._repeatDelay= 0.4;  // seconds before repeat starts
    this._repeatRate = 0.05; // seconds between repeats

    // ── Mouse ──
    this.mouse = {
      x: 0, y: 0,       // screen space
      wx: 0, wy: 0,     // world space
      dx: 0, dy: 0,     // delta this frame
      buttons: {},      // button index → { down, justPressed, justReleased }
      wheel: 0,
      dragging: false,
      dragStart: { x:0, y:0 },
      dragDelta: { x:0, y:0 },
    };
    this._prevMouseX = 0;
    this._prevMouseY = 0;

    // ── Touch ──
    this.touch = {
      active: false,
      touches: [],       // active touch points
      pinchDist: 0,
      pinchScale: 1,
      tapTimer: 0,
      doubleTap: false,
    };

    // ── Virtual Joystick (mobile) ──
    this.joystick = {
      active: false,
      base: { x:0, y:0 },
      stick: { x:0, y:0 },
      dx: 0, dy: 0,      // normalized -1..1
      radius: 60,
      visible: false,
    };

    // ── Gamepad ──
    this._gamepads = {};

    this._setupListeners();
  }

  _setupListeners() {
    const canvas = this.engine.canvas;

    // ── Keyboard ──
    window.addEventListener("keydown", e => {
      const code = e.code;
      const prev = this._keys[code];

      // Prevent browser defaults
      const prevent = ["Space","ArrowUp","ArrowDown","ArrowLeft","ArrowRight","Tab"];
      if (prevent.includes(code)) e.preventDefault();

      if (!prev?.down) {
        this._keys[code] = { down:true, justPressed:true, justReleased:false, timer:0 };
        this._keyBuffer.push(code);
        if (this._keyBuffer.length > 16) this._keyBuffer.shift();
        this.engine.emit("input:keydown", { code, key:e.key });
      } else {
        // Repeat
        prev.timer = prev.timer || 0;
      }
    });

    window.addEventListener("keyup", e => {
      const code = e.code;
      if (this._keys[code]) {
        this._keys[code].down         = false;
        this._keys[code].justReleased = true;
      }
      this.engine.emit("input:keyup", { code, key:e.key });
    });

    // ── Mouse Move ──
    canvas.addEventListener("mousemove", e => {
      const pos = this._canvasPos(e.clientX, e.clientY);
      this.mouse.x  = pos.x;
      this.mouse.y  = pos.y;
      const wp = this.engine.camera.screenToWorld(pos.x, pos.y);
      this.mouse.wx = wp.x;
      this.mouse.wy = wp.y;

      if (this.mouse.buttons[0]?.down) {
        this.mouse.dragging  = true;
        this.mouse.dragDelta = {
          x: pos.x - this.mouse.dragStart.x,
          y: pos.y - this.mouse.dragStart.y,
        };
      }
    });

    // ── Mouse Down ──
    canvas.addEventListener("mousedown", e => {
      const pos = this._canvasPos(e.clientX, e.clientY);
      this.mouse.buttons[e.button] = { down:true, justPressed:true, justReleased:false };
      this.mouse.dragStart = { x:pos.x, y:pos.y };
      this.mouse.dragging  = false;
      this.engine.emit("input:mousedown", {
        button:e.button,
        x:this.mouse.x, y:this.mouse.y,
        wx:this.mouse.wx, wy:this.mouse.wy,
      });
    });

    // ── Mouse Up ──
    canvas.addEventListener("mouseup", e => {
      if (this.mouse.buttons[e.button]) {
        this.mouse.buttons[e.button].down         = false;
        this.mouse.buttons[e.button].justReleased = true;
      }
      if (!this.mouse.dragging) {
        this.engine.emit("input:click", {
          button:e.button,
          x:this.mouse.x, y:this.mouse.y,
          wx:this.mouse.wx, wy:this.mouse.wy,
        });
      }
      this.mouse.dragging = false;
      this.engine.emit("input:mouseup", { button:e.button });
    });

    // ── Mouse Wheel ──
    canvas.addEventListener("wheel", e => {
      e.preventDefault();
      this.mouse.wheel = e.deltaY > 0 ? -1 : 1;
      const delta = e.deltaY > 0 ? -0.12 : 0.12;
      this.engine.camera.setZoom(this.engine.camera.zoom + delta);
      this.engine.emit("input:wheel", { delta, raw:e.deltaY });
    }, { passive:false });

    // ── Context menu disable ──
    canvas.addEventListener("contextmenu", e => e.preventDefault());

    // ── Touch ──
    canvas.addEventListener("touchstart",  e => { e.preventDefault(); this._onTouchStart(e); },  { passive:false });
    canvas.addEventListener("touchmove",   e => { e.preventDefault(); this._onTouchMove(e); },   { passive:false });
    canvas.addEventListener("touchend",    e => { e.preventDefault(); this._onTouchEnd(e); },    { passive:false });
    canvas.addEventListener("touchcancel", e => { e.preventDefault(); this._onTouchEnd(e); },    { passive:false });

    // ── Gamepad ──
    window.addEventListener("gamepadconnected",    e => { this._gamepads[e.gamepad.index] = e.gamepad; console.log("[Input] Gamepad connected"); });
    window.addEventListener("gamepaddisconnected", e => { delete this._gamepads[e.gamepad.index]; });
  }

  // ─────────────────────────────────────────────
  // TOUCH
  // ─────────────────────────────────────────────
  _onTouchStart(e) {
    this.touch.active  = true;
    this.touch.touches = Array.from(e.touches).map(t => {
      const pos = this._canvasPos(t.clientX, t.clientY);
      return { id:t.identifier, x:pos.x, y:pos.y };
    });

    if (e.touches.length === 1) {
      const pos = this.touch.touches[0];

      // Show virtual joystick on left side
      if (pos.x < this.engine.config.width * 0.4) {
        this.joystick.active  = true;
        this.joystick.visible = true;
        this.joystick.base    = { x:pos.x, y:pos.y };
        this.joystick.stick   = { x:pos.x, y:pos.y };
      } else {
        // Right side = tap/click
        const wp = this.engine.camera.screenToWorld(pos.x, pos.y);
        this.mouse.x = pos.x; this.mouse.y = pos.y;
        this.mouse.wx = wp.x; this.mouse.wy = wp.y;
        this.engine.emit("input:mousedown", { button:0, x:pos.x, y:pos.y, wx:wp.x, wy:wp.y });
      }

      // Double tap detection
      if (this.touch.tapTimer > 0) {
        this.touch.doubleTap = true;
        this.engine.emit("input:doubletap", { x:pos.x, y:pos.y });
      }
      this.touch.tapTimer = 0.3;
    }

    // Pinch start
    if (e.touches.length === 2) {
      this.joystick.active = false;
      const dx = this.touch.touches[0].x - this.touch.touches[1].x;
      const dy = this.touch.touches[0].y - this.touch.touches[1].y;
      this.touch.pinchDist  = Math.sqrt(dx*dx + dy*dy);
      this.touch.pinchScale = 1;
    }
  }

  _onTouchMove(e) {
    this.touch.touches = Array.from(e.touches).map(t => {
      const pos = this._canvasPos(t.clientX, t.clientY);
      return { id:t.identifier, x:pos.x, y:pos.y };
    });

    if (e.touches.length === 1 && this.joystick.active) {
      const pos = this.touch.touches[0];
      const dx  = pos.x - this.joystick.base.x;
      const dy  = pos.y - this.joystick.base.y;
      const dist= Math.sqrt(dx*dx + dy*dy);
      const max = this.joystick.radius;
      const nx  = dist > max ? (dx/dist)*max : dx;
      const ny  = dist > max ? (dy/dist)*max : dy;
      this.joystick.stick = { x: this.joystick.base.x + nx, y: this.joystick.base.y + ny };
      this.joystick.dx    = nx / max;
      this.joystick.dy    = ny / max;
    }

    // Pinch zoom
    if (e.touches.length === 2 && this.touch.pinchDist > 0) {
      const dx   = this.touch.touches[0].x - this.touch.touches[1].x;
      const dy   = this.touch.touches[0].y - this.touch.touches[1].y;
      const dist = Math.sqrt(dx*dx + dy*dy);
      const delta = (dist - this.touch.pinchDist) * 0.004;
      this.engine.camera.setZoom(this.engine.camera.zoom + delta);
      this.touch.pinchDist = dist;
    }
  }

  _onTouchEnd(e) {
    if (e.touches.length === 0) {
      this.touch.active    = false;
      this.touch.pinchDist = 0;
      if (this.joystick.active) {
        this.joystick.active  = false;
        this.joystick.visible = false;
        this.joystick.dx      = 0;
        this.joystick.dy      = 0;
      } else {
        this.engine.emit("input:mouseup", { button:0 });
        if (!this.mouse.dragging) {
          this.engine.emit("input:click", { button:0, x:this.mouse.x, y:this.mouse.y, wx:this.mouse.wx, wy:this.mouse.wy });
        }
      }
    } else {
      this.touch.touches = Array.from(e.touches).map(t => {
        const pos = this._canvasPos(t.clientX, t.clientY);
        return { id:t.identifier, x:pos.x, y:pos.y };
      });
    }
  }

  // ─────────────────────────────────────────────
  // QUERY METHODS
  // ─────────────────────────────────────────────
  isDown(code)          { return !!this._keys[code]?.down; }
  wasJustPressed(code)  { return !!this._keys[code]?.justPressed; }
  wasJustReleased(code) { return !!this._keys[code]?.justReleased; }
  isMouseDown(btn)      { return !!this.mouse.buttons[btn]?.down; }
  mouseJustPressed(btn) { return !!this.mouse.buttons[btn]?.justPressed; }
  mouseJustReleased(btn){ return !!this.mouse.buttons[btn]?.justReleased; }

  // Axis helpers
  axis(negCode, posCode) {
    return (this.isDown(posCode)?1:0) - (this.isDown(negCode)?1:0);
  }
  axisH() { return this.axis("KeyA","KeyD") || this.axis("ArrowLeft","ArrowRight") || this.joystick.dx; }
  axisV() { return this.axis("KeyW","KeyS") || this.axis("ArrowUp","ArrowDown")    || this.joystick.dy; }

  // ─────────────────────────────────────────────
  // UPDATE — clear justPressed/justReleased each frame
  // ─────────────────────────────────────────────
  update(dt) {
    // Clear just pressed/released
    Object.values(this._keys).forEach(k => {
      k.justPressed  = false;
      k.justReleased = false;
    });
    Object.values(this.mouse.buttons).forEach(b => {
      b.justPressed  = false;
      b.justReleased = false;
    });
    this.mouse.wheel = 0;
    this.touch.doubleTap = false;

    // Double tap timer
    if (this.touch.tapTimer > 0) this.touch.tapTimer -= dt;

    // Mouse delta
    this.mouse.dx = this.mouse.x - this._prevMouseX;
    this.mouse.dy = this.mouse.y - this._prevMouseY;
    this._prevMouseX = this.mouse.x;
    this._prevMouseY = this.mouse.y;

    // Camera pan from keyboard/joystick
    const cam   = this.engine.camera;
    const speed = 280 * dt / cam.zoom;
    const h = this.axisH(), v = this.axisV();
    if (h !== 0) cam._targetX += h * speed;
    if (v !== 0) cam._targetY += v * speed;

    // Gamepad
    this._updateGamepads(dt);
  }

  _updateGamepads(dt) {
    const gpList = navigator.getGamepads?.() || [];
    for (const gp of gpList) {
      if (!gp) continue;
      const lx = Math.abs(gp.axes[0]) > 0.15 ? gp.axes[0] : 0;
      const ly = Math.abs(gp.axes[1]) > 0.15 ? gp.axes[1] : 0;
      const cam = this.engine.camera;
      const speed = 280 * dt / cam.zoom;
      cam._targetX += lx * speed;
      cam._targetY += ly * speed;
    }
  }

  // ─────────────────────────────────────────────
  // RENDER — virtual joystick
  // ─────────────────────────────────────────────
  render(ctx) {
    if (!this.joystick.visible) return;
    const j = this.joystick;
    ctx.globalAlpha = 0.4;
    // Base circle
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth   = 2;
    ctx.beginPath();
    ctx.arc(j.base.x, j.base.y, j.radius, 0, Math.PI*2);
    ctx.stroke();
    // Stick
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.arc(j.stick.x, j.stick.y, 20, 0, Math.PI*2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  _canvasPos(clientX, clientY) {
    const rect   = this.engine.canvas.getBoundingClientRect();
    const scaleX = this.engine.config.width  / rect.width;
    const scaleY = this.engine.config.height / rect.height;
    return {
      x: (clientX - rect.left) * scaleX,
      y: (clientY - rect.top)  * scaleY,
    };
  }
}
