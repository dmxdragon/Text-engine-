/**
 * Nixon RPG Engine — Entity System
 * Base Entity, Component System, State Machine, Object Pool
 */

// ═══════════════════════════════════════════════════════════
// COMPONENT BASE
// ═══════════════════════════════════════════════════════════
class Component {
  constructor(entity) {
    this.entity  = entity;
    this.engine  = entity.engine;
    this.enabled = true;
  }
  init()       {}
  update(dt)   {}
  render(ctx)  {}
  destroy()    {}
}

// ═══════════════════════════════════════════════════════════
// BASE ENTITY (GameObject)
// ═══════════════════════════════════════════════════════════
class Entity {
  constructor(engine, x = 0, y = 0) {
    this.engine     = engine;
    this.id         = Entity._nextId++;

    // Transform
    this.x          = x;
    this.y          = y;
    this.w          = 32;
    this.h          = 32;
    this.rotation   = 0;    // radians
    this.scaleX     = 1;
    this.scaleY     = 1;
    this.originX    = 0.5;  // pivot 0..1
    this.originY    = 0.5;

    // Physics
    this.vx         = 0;
    this.vy         = 0;
    this.friction   = 0.85;
    this.mass       = 1;
    this.physics    = false;
    this.collides   = false;
    this.static     = false; // true = immovable

    // State
    this.active     = true;
    this.visible    = true;
    this.dead       = false;

    // Layer (z-order)
    this.layer      = 0;

    // Tags
    this.tags       = new Set();

    // Components
    this._components= new Map();

    // Children
    this.parent     = null;
    this.children   = [];

    // Scene ref (set by scene.add)
    this.scene      = null;
  }

  // ─────────────────────────────────────────────
  // LIFECYCLE
  // ─────────────────────────────────────────────
  init()      {}
  update(dt)  {
    if (!this.active) return;
    this._components.forEach(c => { if (c.enabled) c.update(dt); });
  }
  render(ctx) {
    if (!this.visible) return;
    this._components.forEach(c => { if (c.enabled) c.render(ctx); });
  }
  destroy() {
    this.dead = true;
    this._components.forEach(c => c.destroy());
    this.children.forEach(child => child.destroy());
    this.engine.physics.unregister(this);
    this.engine.emit("entity:destroyed", { entity: this });
  }

  // ─────────────────────────────────────────────
  // COMPONENTS
  // ─────────────────────────────────────────────
  addComponent(name, component) {
    this._components.set(name, component);
    component.init();
    return component;
  }

  getComponent(name)    { return this._components.get(name) || null; }
  hasComponent(name)    { return this._components.has(name); }
  removeComponent(name) {
    const c = this._components.get(name);
    c?.destroy();
    this._components.delete(name);
  }

  // ─────────────────────────────────────────────
  // HIERARCHY
  // ─────────────────────────────────────────────
  addChild(entity) {
    entity.parent = this;
    this.children.push(entity);
    return entity;
  }

  removeChild(entity) {
    this.children = this.children.filter(c => c !== entity);
    entity.parent = null;
  }

  get worldX() { return this.parent ? this.parent.worldX + this.x : this.x; }
  get worldY() { return this.parent ? this.parent.worldY + this.y : this.y; }

  // ─────────────────────────────────────────────
  // HELPERS
  // ─────────────────────────────────────────────
  distanceTo(other) {
    const dx = other.x - this.x, dy = other.y - this.y;
    return Math.sqrt(dx*dx + dy*dy);
  }

  angleTo(other) {
    return Math.atan2(other.y - this.y, other.x - this.x);
  }

  moveTowards(tx, ty, speed, dt) {
    const dx   = tx - this.x, dy = ty - this.y;
    const dist = Math.sqrt(dx*dx + dy*dy);
    if (dist < 1) return true;
    const step = Math.min(speed * dt, dist);
    this.x += (dx/dist) * step;
    this.y += (dy/dist) * step;
    return dist <= step;
  }

  overlaps(other) {
    const hw = this.w/2, hh = this.h/2;
    const ow = other.w/2, oh = other.h/2;
    return Math.abs(this.x - other.x) < hw+ow &&
           Math.abs(this.y - other.y) < hh+oh;
  }

  hasTag(tag)    { return this.tags.has(tag); }
  addTag(tag)    { this.tags.add(tag); return this; }
  removeTag(tag) { this.tags.delete(tag); return this; }
}
Entity._nextId = 1;

// ═══════════════════════════════════════════════════════════
// STATE MACHINE
// ═══════════════════════════════════════════════════════════
class StateMachine {
  constructor(entity, states = {}) {
    this.entity  = entity;
    this.states  = {};
    this.current = null;
    this.previous= null;
    this._timer  = 0;

    Object.entries(states).forEach(([name, def]) => this.addState(name, def));
  }

  addState(name, def) {
    // def = { enter, update, exit, transitions }
    // transitions = [ { to, condition } ]
    this.states[name] = {
      name,
      enter:       def.enter       || (() => {}),
      update:      def.update      || (() => {}),
      exit:        def.exit        || (() => {}),
      transitions: def.transitions || [],
    };
    return this;
  }

  start(name) {
    const state = this.states[name];
    if (!state) { console.warn(`[StateMachine] Unknown state: ${name}`); return; }
    this.current = state;
    this._timer  = 0;
    state.enter(this.entity);
  }

  transition(name) {
    if (!this.states[name]) return;
    if (this.current) {
      this.current.exit(this.entity);
      this.previous = this.current;
    }
    this.current = this.states[name];
    this._timer  = 0;
    this.current.enter(this.entity);
    this.entity.engine?.emit("statemachine:transition", { entity:this.entity, from:this.previous?.name, to:name });
  }

  update(dt) {
    if (!this.current) return;
    this._timer += dt;
    this.current.update(this.entity, dt, this._timer);

    // Check transitions
    for (const t of this.current.transitions) {
      if (t.condition(this.entity, this._timer)) {
        this.transition(t.to);
        break;
      }
    }
  }

  is(name)    { return this.current?.name === name; }
  was(name)   { return this.previous?.name === name; }
  get state() { return this.current?.name || null; }
  get elapsed(){ return this._timer; }
}

// Built-in state machines for common entity types
function createCharacterSM(entity) {
  return new StateMachine(entity, {
    idle: {
      enter: (e) => e.animator?.play("idle"),
      update: (e) => {
        if (Math.abs(e.vx) > 5 || Math.abs(e.vy) > 5) return;
      },
      transitions: [
        { to:"walk",   condition: (e) => Math.abs(e.vx) > 5 || Math.abs(e.vy) > 5 },
        { to:"dead",   condition: (e) => e.hp <= 0 },
      ]
    },
    walk: {
      enter: (e) => e.animator?.play("walk"),
      transitions: [
        { to:"idle",   condition: (e) => Math.abs(e.vx) < 2 && Math.abs(e.vy) < 2 },
        { to:"dead",   condition: (e) => e.hp <= 0 },
      ]
    },
    attack: {
      enter: (e) => e.animator?.play("attack", () => e.sm?.transition("idle")),
      transitions: [
        { to:"dead",   condition: (e) => e.hp <= 0 },
      ]
    },
    hurt: {
      enter: (e) => e.animator?.play("hurt", () => e.sm?.transition("idle")),
      transitions: [
        { to:"dead",   condition: (e) => e.hp <= 0 },
      ]
    },
    dead: {
      enter: (e) => {
        e.animator?.play("death", () => e.destroy());
        e.collides = false;
        e.engine?.particles.deathEffect(e.x, e.y);
      },
      transitions: []
    },
  });
}

function createMonsterSM(entity, target = null) {
  return new StateMachine(entity, {
    patrol: {
      enter: (e) => {
        e.animator?.play("idle");
        e._patrolTimer = 0;
        e._patrolTarget = null;
      },
      update: (e, dt) => {
        e._patrolTimer = (e._patrolTimer||0) + dt;
        if (!e._patrolTarget || e._patrolTimer > 3) {
          e._patrolTimer  = 0;
          const angle     = Math.random() * Math.PI * 2;
          const dist      = 30 + Math.random() * 60;
          e._patrolTarget = { x: e.x + Math.cos(angle)*dist, y: e.y + Math.sin(angle)*dist };
        }
        if (e._patrolTarget) {
          e.moveTowards(e._patrolTarget.x, e._patrolTarget.y, 40, dt);
        }
      },
      transitions: [
        { to:"chase",  condition: (e) => e._target && e.distanceTo(e._target) < 200 },
        { to:"dead",   condition: (e) => e.hp <= 0 },
      ]
    },
    chase: {
      enter: (e) => e.animator?.play("walk"),
      update: (e, dt) => {
        if (e._target) e.moveTowards(e._target.x, e._target.y, 90, dt);
      },
      transitions: [
        { to:"attack", condition: (e) => e._target && e.distanceTo(e._target) < 40 },
        { to:"patrol", condition: (e) => !e._target || e.distanceTo(e._target) > 300 },
        { to:"dead",   condition: (e) => e.hp <= 0 },
      ]
    },
    attack: {
      enter: (e) => {
        e.animator?.play("attack", () => e.sm?.transition("chase"));
        if (e._target) e.engine.emit("monster:attack", { monster:e, target:e._target });
      },
      transitions: [
        { to:"dead",   condition: (e) => e.hp <= 0 },
        { to:"patrol", condition: (e) => !e._target },
      ]
    },
    dead: {
      enter: (e) => {
        e.animator?.play("death", () => e.destroy());
        e.collides = false;
        e.engine.particles.deathEffect(e.x, e.y);
        e.engine.emit("monster:died", { monster:e });
      },
      transitions: []
    },
  });
}

// ═══════════════════════════════════════════════════════════
// OBJECT POOL
// ═══════════════════════════════════════════════════════════
class ObjectPool {
  constructor(factory, reset, initialSize = 20) {
    this._factory  = factory;  // () => new Object()
    this._reset    = reset;    // (obj) => reset its state
    this._pool     = [];
    this._active   = new Set();

    // Pre-allocate
    for (let i = 0; i < initialSize; i++) {
      this._pool.push(this._factory());
    }
  }

  // Get an object from pool
  get(...args) {
    let obj = this._pool.pop();
    if (!obj) {
      obj = this._factory();
      console.log("[Pool] Growing pool");
    }
    this._reset(obj, ...args);
    obj._poolRef = this;
    this._active.add(obj);
    return obj;
  }

  // Return object to pool
  release(obj) {
    if (!this._active.has(obj)) return;
    this._active.delete(obj);
    obj.active = false;
    obj.visible= false;
    obj.dead   = false;
    this._pool.push(obj);
  }

  // Release all active objects
  releaseAll() {
    this._active.forEach(obj => this.release(obj));
  }

  get activeCount() { return this._active.size; }
  get poolSize()    { return this._pool.length; }

  update(dt) {
    this._active.forEach(obj => {
      obj.update?.(dt);
      if (obj.dead) this.release(obj);
    });
  }

  render(ctx) {
    this._active.forEach(obj => {
      if (obj.visible) obj.render?.(ctx);
    });
  }
}

// Built-in pools
class BulletPool extends ObjectPool {
  constructor(engine) {
    super(
      () => ({
        engine, active:false, visible:false, dead:false,
        x:0, y:0, vx:0, vy:0, damage:10, life:2, maxLife:2, r:4,
        color:"#ffd700", owner:null,
        update(dt) {
          if (!this.active) return;
          this.x += this.vx * dt;
          this.y += this.vy * dt;
          this.life -= dt;
          if (this.life <= 0) this.dead = true;
        },
        render(ctx) {
          if (!this.visible) return;
          ctx.fillStyle   = this.color;
          ctx.globalAlpha = this.life / this.maxLife;
          ctx.beginPath();
          ctx.arc(this.x, this.y, this.r, 0, Math.PI*2);
          ctx.fill();
          ctx.globalAlpha = 1;
        }
      }),
      (obj, x, y, vx, vy, damage=10, color="#ffd700") => {
        obj.active=true; obj.visible=true; obj.dead=false;
        obj.x=x; obj.y=y; obj.vx=vx; obj.vy=vy;
        obj.damage=damage; obj.color=color;
        obj.life=2; obj.maxLife=2;
      },
      30
    );
  }
}
