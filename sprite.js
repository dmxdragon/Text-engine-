/**
 * Nixon RPG Engine — Sprite & Animation
 * Animated entities, characters, monsters
 */

// ═══════════════════════════════════════════════════════════
// ANIMATOR — manages sprite animations
// ═══════════════════════════════════════════════════════════
class Animator {
  constructor(sheet) {
    this.sheet       = sheet;
    this.current     = null;
    this.frameIndex  = 0;
    this._timer      = 0;
    this._onComplete = null;
    this.flipX       = false;
    this.playing     = false;
  }

  play(animName, onComplete = null) {
    if (!this.sheet) return;
    const anim = this.sheet.animations[animName];
    if (!anim) return;

    if (this.current === animName && anim.loop) return; // already playing

    this.current     = animName;
    this.frameIndex  = 0;
    this._timer      = 0;
    this._onComplete = onComplete;
    this.playing     = true;
  }

  update(dt) {
    if (!this.sheet || !this.current || !this.playing) return;
    const anim = this.sheet.animations[this.current];
    if (!anim) return;

    this._timer += dt;
    const frameDur = 1 / anim.fps;

    if (this._timer >= frameDur) {
      this._timer -= frameDur;
      this.frameIndex++;

      if (this.frameIndex >= anim.frameCount) {
        if (anim.loop) {
          this.frameIndex = 0;
        } else {
          this.frameIndex = anim.frameCount - 1;
          this.playing    = false;
          this._onComplete?.();
        }
      }
    }
  }

  draw(ctx, x, y, w, h) {
    if (!this.sheet || !this.current) return;
    this.sheet.draw(ctx, this.current, this.frameIndex, x, y, w, h, this.flipX);
  }
}

// ═══════════════════════════════════════════════════════════
// SPRITE ENTITY (extends Entity from entity.js)
// ═══════════════════════════════════════════════════════════
class SpriteEntity extends Entity {
  constructor(engine, x, y, sheetName) {
    super(engine, x, y);
    this.sheetName = sheetName;
    this.animator  = null;
    this.scale     = 1;

    const sheet = engine.loader?.getSheet(sheetName);
    if (sheet) {
      this.animator = new Animator(sheet);
      this.w        = sheet.frameW;
      this.h        = sheet.frameH;
    }
  }

  play(anim, onComplete) {
    this.animator?.play(anim, onComplete);
  }

  update(dt) {
    this.animator?.update(dt);
  }

  render(ctx) {
    if (!this.animator) {
      // Fallback: colored rect
      ctx.fillStyle = "#ff00ff";
      ctx.fillRect(this.x - this.w/2, this.y - this.h/2, this.w, this.h);
      return;
    }
    const dw = this.w  * this.scale;
    const dh = this.h  * this.scale;
    this.animator.draw(ctx, this.x - dw/2, this.y - dh/2, dw, dh);
  }
}

// ═══════════════════════════════════════════════════════════
// CHARACTER ENTITY — hero / player character
// ═══════════════════════════════════════════════════════════
class CharacterEntity extends SpriteEntity {
  constructor(engine, x, y, data) {
    super(engine, x, y, `hero_${data.archetype || "warrior"}`);
    this.data     = data;
    this.physics  = true;
    this.collides = true;
    this.scale    = 1.5;

    // Stats from API
    this.stats    = data.stats || {};
    this.isNFT    = data.is_nft || false;

    // State machine
    this.state    = "idle"; // idle | walk | attack | hurt | dead

    // Name label
    this._labelAlpha = 1;
    this._hoverTimer = 0;

    // Bobbing animation
    this._bobTimer = Math.random() * Math.PI * 2;

    this.play("idle");
    engine.physics.register(this);
  }

  setState(newState) {
    if (this.state === newState) return;
    this.state = newState;
    switch(newState) {
      case "idle":   this.play("idle"); break;
      case "walk":   this.play("walk"); break;
      case "attack": this.play("attack", () => this.setState("idle")); break;
      case "hurt":   this.play("hurt",   () => this.setState("idle")); break;
      case "dead":   this.play("death",  () => { this.dead = true; }); break;
    }
  }

  update(dt) {
    super.update(dt);
    this._bobTimer += dt * 1.5;

    // Visual bob when idle
    if (this.state === "idle") {
      this._visualY = Math.sin(this._bobTimer) * 2;
    } else {
      this._visualY = 0;
    }
  }

  render(ctx) {
    const drawY = this.y + (this._visualY || 0);

    // Shadow
    ctx.fillStyle = "rgba(0,0,0,0.3)";
    ctx.beginPath();
    ctx.ellipse(this.x, this.y + this.h/2 * this.scale * 0.4, this.w * 0.35 * this.scale, 4, 0, 0, Math.PI*2);
    ctx.fill();

    // NFT glow
    if (this.isNFT) {
      ctx.shadowBlur  = 12;
      ctx.shadowColor = "#ffd700";
    }

    // Draw sprite or fallback
    if (this.animator) {
      const dw = this.w * this.scale;
      const dh = this.h * this.scale;
      this.animator.flipX = this.vx < -0.5;
      this.animator.draw(ctx, this.x - dw/2, drawY - dh/2, dw, dh);
    } else {
      this._drawFallback(ctx, drawY);
    }

    ctx.shadowBlur = 0;

    // Name label
    this._renderLabel(ctx, drawY);
  }

  _drawFallback(ctx, drawY) {
    const s  = this.scale;
    const px = this.x, py = drawY;
    const c  = this.isNFT ? "#ffd700" : "#4488ff";
    ctx.fillStyle = c;
    ctx.fillRect(px - 10*s, py - 20*s, 20*s, 28*s);
    ctx.fillStyle = "#f0d080";
    ctx.fillRect(px - 7*s, py - 32*s, 14*s, 14*s);
    if (this.isNFT) {
      ctx.fillStyle = "#ffd700";
      ctx.fillRect(px - 8*s, py - 38*s, 6*s, 8*s);
      ctx.fillRect(px - 3*s, py - 42*s, 6*s, 12*s);
      ctx.fillRect(px + 2*s, py - 38*s, 6*s, 8*s);
    }
  }

  _renderLabel(ctx, drawY) {
    const name  = (this.data.name || "?").split(" ")[0];
    const color = this.isNFT ? "#ffd700" : "#aaaacc";
    ctx.fillStyle   = color;
    ctx.font        = "10px 'VT323', monospace";
    ctx.textAlign   = "center";
    ctx.globalAlpha = 0.85;
    ctx.fillText(name, this.x, drawY - this.h * this.scale * 0.6);
    ctx.textAlign   = "left";
    ctx.globalAlpha = 1;
  }

  destroy() {
    this.engine.physics.unregister(this);
    super.destroy();
  }
}

// ═══════════════════════════════════════════════════════════
// MONSTER ENTITY
// ═══════════════════════════════════════════════════════════
class MonsterEntity extends SpriteEntity {
  constructor(engine, x, y, data) {
    const tier = data.tier || "common";
    super(engine, x, y, `monster_${tier}`);
    this.data    = data;
    this.physics = false;
    this.scale   = data.is_boss ? 2.5 : 1.5;

    this._bobTimer = Math.random() * Math.PI * 2;
    this._wanderTimer = Math.random() * 3;
    this._wanderDX = 0;
    this._wanderDY = 0;

    this.play("idle");
    engine.physics.register(this);
  }

  update(dt) {
    super.update(dt);
    this._bobTimer += dt;

    // Wander slightly
    this._wanderTimer -= dt;
    if (this._wanderTimer <= 0) {
      this._wanderTimer = 2 + Math.random() * 3;
      const angle = Math.random() * Math.PI * 2;
      const speed = this.data.is_boss ? 8 : 15;
      this._wanderDX = Math.cos(angle) * speed;
      this._wanderDY = Math.sin(angle) * speed;
    }
    this.x += this._wanderDX * dt * 0.3;
    this.y += this._wanderDY * dt * 0.3;
  }

  render(ctx) {
    const bob  = Math.sin(this._bobTimer * 1.2) * (this.data.is_boss ? 4 : 2);
    const drawY = this.y + bob;

    // Shadow
    ctx.fillStyle = "rgba(0,0,0,0.4)";
    ctx.beginPath();
    ctx.ellipse(this.x, this.y + this.h/2 * this.scale * 0.3, this.w * 0.3 * this.scale, 5, 0, 0, Math.PI*2);
    ctx.fill();

    // Boss red glow
    if (this.data.is_boss) {
      ctx.shadowBlur  = 20;
      ctx.shadowColor = "#cc0000";
    }

    if (this.animator) {
      const dw = this.w * this.scale;
      const dh = this.h * this.scale;
      this.animator.draw(ctx, this.x - dw/2, drawY - dh/2, dw, dh);
    } else {
      this._drawFallback(ctx, drawY);
    }

    ctx.shadowBlur = 0;
    this._renderLabel(ctx, drawY);
    if (this.data.is_boss) this._renderBossHP(ctx, drawY);
  }

  _drawFallback(ctx, drawY) {
    const s = this.scale;
    ctx.fillStyle = this.data.is_boss ? "#cc0000" : "#882222";
    ctx.fillRect(this.x - 12*s, drawY - 14*s, 24*s, 20*s);
    ctx.fillStyle = "#ff2200";
    ctx.fillRect(this.x - 7*s, drawY - 18*s, 5*s, 5*s);
    ctx.fillRect(this.x + 2*s, drawY - 18*s, 5*s, 5*s);
    if (this.data.is_boss) {
      ctx.fillStyle = "#ff0000";
      for (let i=-1;i<=1;i++) ctx.fillRect(this.x + i*8*s - 2*s, drawY - 20*s, 4*s, 8*s);
    }
  }

  _renderLabel(ctx, drawY) {
    const color = this.data.is_boss ? "#ff4444" : "#cc4444";
    ctx.fillStyle   = color;
    ctx.font        = "10px 'VT323', monospace";
    ctx.textAlign   = "center";
    ctx.globalAlpha = 0.9;
    ctx.fillText(this.data.name?.split(" ")[0] || "?", this.x, drawY - this.h * this.scale * 0.6);
    ctx.textAlign   = "left";
    ctx.globalAlpha = 1;
  }

  _renderBossHP(ctx, drawY) {
    const pct = (this.data.hp / this.data.max_hp) || 1;
    const bw  = 60, bh = 5;
    const bx  = this.x - bw/2;
    const by  = drawY - this.h * this.scale * 0.7 - 14;
    ctx.fillStyle = "#330000";
    ctx.fillRect(bx, by, bw, bh);
    ctx.fillStyle = "#cc2200";
    ctx.fillRect(bx, by, bw * pct, bh);
    ctx.strokeStyle = "#660000";
    ctx.lineWidth   = 1;
    ctx.strokeRect(bx, by, bw, bh);
  }

  destroy() {
    this.engine.physics.unregister(this);
    super.destroy();
  }
}

// ═══════════════════════════════════════════════════════════
// FLOATING TEXT ENTITY
// ═══════════════════════════════════════════════════════════
class FloatingText extends Entity {
  constructor(engine, x, y, text, color = "#ffd700", size = 16) {
    super(engine, x, y);
    this.text    = text;
    this.color   = color;
    this.size    = size;
    this._life   = 1.2;
    this._maxLife= 1.2;
    this.vy      = -40;
  }

  update(dt) {
    this._life -= dt;
    this.y     += this.vy * dt;
    this.vy    *= 0.95;
    if (this._life <= 0) this.dead = true;
  }

  render(ctx) {
    const alpha = Math.max(0, this._life / this._maxLife);
    ctx.globalAlpha = alpha;
    ctx.fillStyle   = this.color;
    ctx.font        = `bold ${this.size}px 'VT323', monospace`;
    ctx.textAlign   = "center";
    ctx.fillText(this.text, this.x, this.y);
    ctx.textAlign   = "left";
    ctx.globalAlpha = 1;
  }
}
