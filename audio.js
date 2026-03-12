/**
 * Nixon RPG Engine — Advanced Audio Engine
 * BGM fade, spatial audio, volume control, mute, pools
 */

class AudioEngine {
  constructor(engine) {
    this.engine   = engine;
    this.context  = null;
    this.master   = null;
    this.sfxGain  = null;
    this.bgmGain  = null;

    this.volume = { master:0.8, sfx:1.0, bgm:0.5 };
    this._muted = false;
    this._bgm   = null;       // { source, gain, name }
    this._sfxPool = {};       // name → [AudioBufferSourceNode]

    this._init();
  }

  _init() {
    try {
      this.context = new (window.AudioContext || window.webkitAudioContext)();
      this.master  = this.context.createGain();
      this.sfxGain = this.context.createGain();
      this.bgmGain = this.context.createGain();

      this.master.gain.value  = this.volume.master;
      this.sfxGain.gain.value = this.volume.sfx;
      this.bgmGain.gain.value = this.volume.bgm;

      this.sfxGain.connect(this.master);
      this.bgmGain.connect(this.master);
      this.master.connect(this.context.destination);

      // Resume on user interaction (browser autoplay policy)
      const resume = () => {
        if (this.context.state === "suspended") this.context.resume();
      };
      document.addEventListener("click",     resume, { once: true });
      document.addEventListener("keydown",   resume, { once: true });
      document.addEventListener("touchstart",resume, { once: true });

    } catch(e) {
      console.warn("[Audio] Web Audio API not supported:", e.message);
    }
  }

  // ─────────────────────────────────────────────
  // SFX
  // ─────────────────────────────────────────────
  play(name, opts = {}) {
    if (!this.context || this._muted) return null;
    const buffer = this.engine.loader.getAudio(name);
    if (!buffer) return null;

    if (this.context.state === "suspended") this.context.resume();

    const source = this.context.createBufferSource();
    const gain   = this.context.createGain();

    source.buffer          = buffer;
    source.playbackRate.value = opts.pitch || 1.0;
    gain.gain.value        = opts.volume || 1.0;

    // Spatial audio (distance-based volume)
    if (opts.x !== undefined && opts.y !== undefined) {
      const cam   = this.engine.camera;
      const cx    = cam.x + this.engine.config.width  / (2 * cam.zoom);
      const cy    = cam.y + this.engine.config.height / (2 * cam.zoom);
      const dist  = Math.sqrt((opts.x-cx)**2 + (opts.y-cy)**2);
      const maxD  = opts.maxDist || 600;
      const spatial = Math.max(0, 1 - dist / maxD);
      gain.gain.value *= spatial;
      if (spatial <= 0) return null; // out of range
    }

    source.connect(gain);
    gain.connect(this.sfxGain);
    source.start(opts.delay || 0);

    // Cleanup
    source.onended = () => source.disconnect();
    return source;
  }

  // Randomize pitch slightly for variation
  playVaried(name, pitchRange = 0.1, volume = 1.0) {
    return this.play(name, {
      pitch:  1.0 + (Math.random() - 0.5) * pitchRange * 2,
      volume,
    });
  }

  // ─────────────────────────────────────────────
  // BGM
  // ─────────────────────────────────────────────
  playBGM(name, fadeIn = 1.5) {
    if (!this.context) return;
    const buffer = this.engine.loader.getAudio(name);
    if (!buffer) return;

    // Same track already playing
    if (this._bgm?.name === name) return;

    // Fade out old
    if (this._bgm) {
      this.stopBGM(0.8);
    }

    if (this.context.state === "suspended") this.context.resume();

    const source = this.context.createBufferSource();
    const gain   = this.context.createGain();
    source.buffer = buffer;
    source.loop   = true;

    gain.gain.setValueAtTime(0, this.context.currentTime);
    gain.gain.linearRampToValueAtTime(
      this._muted ? 0 : this.volume.bgm,
      this.context.currentTime + fadeIn
    );

    source.connect(gain);
    gain.connect(this.bgmGain);
    source.start(0);

    this._bgm = { source, gain, name };
  }

  stopBGM(fadeOut = 1.0) {
    if (!this._bgm || !this.context) return;
    const { source, gain } = this._bgm;
    const now = this.context.currentTime;
    gain.gain.cancelScheduledValues(now);
    gain.gain.setValueAtTime(gain.gain.value, now);
    gain.gain.linearRampToValueAtTime(0, now + fadeOut);
    setTimeout(() => {
      try { source.stop(); source.disconnect(); } catch(e) {}
    }, (fadeOut + 0.1) * 1000);
    this._bgm = null;
  }

  crossfadeBGM(name, duration = 2.0) {
    this.stopBGM(duration * 0.6);
    setTimeout(() => this.playBGM(name, duration * 0.4), duration * 0.4 * 1000);
  }

  // ─────────────────────────────────────────────
  // VOLUME / MUTE
  // ─────────────────────────────────────────────
  setMasterVolume(v) {
    this.volume.master = Math.max(0, Math.min(1, v));
    if (this.master) this.master.gain.value = this._muted ? 0 : this.volume.master;
  }

  setSFXVolume(v) {
    this.volume.sfx = Math.max(0, Math.min(1, v));
    if (this.sfxGain) this.sfxGain.gain.value = this.volume.sfx;
  }

  setBGMVolume(v) {
    this.volume.bgm = Math.max(0, Math.min(1, v));
    if (this.bgmGain) this.bgmGain.gain.value = this.volume.bgm;
  }

  mute() {
    this._muted = true;
    if (this.master) this.master.gain.value = 0;
  }

  unmute() {
    this._muted = false;
    if (this.master) this.master.gain.value = this.volume.master;
  }

  toggleMute() {
    this._muted ? this.unmute() : this.mute();
    return this._muted;
  }

  get isMuted() { return this._muted; }

  // Generate simple procedural sounds (no assets needed)
  beep(freq = 440, duration = 0.1, type = "square") {
    if (!this.context) return;
    if (this.context.state === "suspended") this.context.resume();
    const osc  = this.context.createOscillator();
    const gain = this.context.createGain();
    osc.type          = type;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.3, this.context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, this.context.currentTime + duration);
    osc.connect(gain);
    gain.connect(this.master);
    osc.start();
    osc.stop(this.context.currentTime + duration);
  }

  // Preset sounds (procedural — no file needed)
  playClick()   { this.beep(800, 0.05, "square"); }
  playHit()     { this.beep(200, 0.08, "sawtooth"); }
  playPickup()  { this.beep(600, 0.15, "sine"); setTimeout(() => this.beep(900, 0.1, "sine"), 80); }
  playDeath()   { this.beep(100, 0.4, "sawtooth"); }
  playLevelUp() {
    [400,500,600,800].forEach((f,i) => setTimeout(() => this.beep(f, 0.12, "sine"), i*80));
  }

  update(dt) {}
}
