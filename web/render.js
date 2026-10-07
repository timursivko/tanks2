/* Рисование арены на canvas: карта, танки, снаряды, конусы зрения, траектории. */

const T = {
  wall: '#3a4250',
  wallTop: '#4a5464',
  column: '#7a6a4f',
  lowCover: '#5a6472',
  mud: '#4a4335',
  floor: '#14171c',
  grid: 'rgba(255,255,255,0.03)',
};

const CHAR = { '#': T.wall, 'o': T.column, ':': T.lowCover, ',': T.mud };

/**: Сколько тиков живёт хвост траектории: 48 тиков = 0.8 с. */
const TRAIL_TTL = 48;
/**: Тайлы, которые не видно насквозь: стены, колонны и низкие укрытия. */
// Непрозрачно для взгляда ровно то, что непрозрачно в движке (config.OPAQUE_TILES).
// Низкое укрытие ':' мешает проезду, но сквозь него видно.
const OPAQUE = new Set(['#', 'o']);

// --- эффекты боя -------------------------------------------------------------

/**: Сколько миллисекунд бой продолжает жить после гибели танка. */
const END_LINGER_MS = 1000;
/**: Цвета сторон — те же, что у танков в tank(). */
const FX_SIDE = ['#ff7043', '#3ea6ff'];

/**
 * Частицы боя: рикошеты, пробитие, уничтожение.
 *
 * Один объект на бой. События превращаются в частицы снаружи (fxEvent), а
 * ArenaView.draw() каждый кадр их двигает и рисует поверх сцены. Часы свои,
 * wall-clock: картинка не зависит от того, на какой скорости проигрывают
 * реплей и стоит ли он на паузе — догоревшие искры всегда догорают.
 */
class Fx {
  constructor() {
    this.parts = [];
    this.rings = [];
    this.texts = [];
    this.shake = 0;          // амплитуда тряски, px
    this.flash = 0;          // вспышка всего экрана, 0..1
    this.flashRGB = '255,255,255';
    this._last = 0;
  }

  clear() {
    this.parts.length = 0;
    this.rings.length = 0;
    this.texts.length = 0;
    this.shake = 0;
    this.flash = 0;
  }

  /** Есть ли что рисовать (включая затухающую тряску и вспышку). */
  get busy() {
    return this.parts.length > 0 || this.rings.length > 0 || this.texts.length > 0
      || this.shake > 0.2 || this.flash > 0.01;
  }

  // --- спавн -----------------------------------------------------------------

  _add(p) {
    if (this.parts.length < 700) this.parts.push(p);
    return p;
  }

  _ring(x, y, r, vr, life, col, w) {
    this.rings.push({ x, y, r, vr, life, max: life, col, w });
  }

  _text(x, y, text, col, size, life) {
    this.texts.push({ x, y, text, col, size, life, max: life, vy: -34 });
  }

  /** Искра: летит по углу ``a`` со скоростью ``sp`` и тянет за собой след. */
  _spark(x, y, a, sp, life, col, w = 1.6) {
    return this._add({ k: 's', x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp,
      life, max: life, col, w, dr: 1.6, g: 0 });
  }

  _fire(x, y, vx, vy, life, r, col) {
    return this._add({ k: 'f', x, y, vx, vy, life, max: life, r, col, dr: 1.2, g: -14 });
  }

  _smoke(x, y, vx, vy, life, r, col = '90,90,96') {
    return this._add({ k: 'm', x, y, vx, vy, life, max: life, r, col,
      dr: 1.8, g: -16, grow: 26 });
  }

  _debris(x, y, a, sp, life, col) {
    return this._add({ k: 'd', x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp,
      life, max: life, col, r: 2 + Math.random() * 3,
      rot: Math.random() * 6.28, vr: (Math.random() - 0.5) * 16, dr: 0.8, g: 320 });
  }

  /** Ядро вспышки в точке: светящееся пятно, живущее долю секунды. */
  _core(x, y, r, life, col) {
    return this._add({ k: 'c', x, y, vx: 0, vy: 0, life, max: life, r, col, dr: 0, g: 0 });
  }

  _flashAt(amount, rgb) {
    if (amount > this.flash) { this.flash = amount; this.flashRGB = rgb; }
  }

  // --- сцены -----------------------------------------------------------------

  /** Выстрел: вспышка у дула, пучок искр по стволу и пороховой дым. */
  muzzle(x, y, ang) {
    const a = ang == null ? Math.random() * Math.PI * 2 : ang;
    this._core(x, y, 13, 0.1, '255,226,150');
    this._ring(x, y, 3, 120, 0.2, 'rgba(255,220,150,0.9)', 2);
    for (let i = 0; i < 8; i++) {
      this._spark(x, y, a + (Math.random() - 0.5) * 0.9,
        170 + Math.random() * 330, 0.16 + Math.random() * 0.16,
        i % 2 ? '#ffd54f' : '#fff8e1');
    }
    this._smoke(x, y, Math.cos(a) * 46, Math.sin(a) * 46, 0.5, 6, '150,150,158');
  }

  /** Рикошет от брони: бело-жёлтый веер искр в отражённом направлении. */
  ricochet(x, y, ang) {
    const a = ang == null ? Math.random() * Math.PI * 2 : ang;
    this._core(x, y, 16, 0.12, '255,240,190');
    this._ring(x, y, 4, 200, 0.3, 'rgba(255,236,170,0.95)', 2.5);
    for (let i = 0; i < 20; i++) {
      const sp = 180 + Math.random() * 430;
      this._spark(x, y, a + (Math.random() - 0.5) * 1.7, sp,
        0.3 + Math.random() * 0.35, i % 3 ? '#ffe082' : '#ffffff', 1.7);
    }
    for (let i = 0; i < 6; i++) {           // угольки, срывающиеся вниз
      const p = this._spark(x, y, Math.random() * 6.283,
        60 + Math.random() * 150, 0.4 + Math.random() * 0.3, '#ffb300', 2);
      p.g = 460;
      p.dr = 1.0;
    }
    this.shake = Math.max(this.shake, 2.5);
    this._flashAt(0.16, '255,236,170');
    this._text(x, y - 12, 'рикошет', '#ffe082', 13, 0.6);
  }

  /** Пробитие брони: вспышка, шрапнель по линии выстрела, дым и урон. */
  impact(x, y, ang, dmg) {
    const a = ang == null ? Math.random() * Math.PI * 2 : ang;
    this._core(x, y, 20, 0.14, '255,206,140');
    this._ring(x, y, 5, 240, 0.36, 'rgba(255,180,120,0.9)', 3);
    this._ring(x, y, 2, 130, 0.5, 'rgba(255,120,80,0.5)', 1.5);
    for (let i = 0; i < 16; i++) {
      const sp = 230 + Math.random() * 470;
      this._spark(x, y, a + (Math.random() - 0.5) * 1.1, sp,
        0.25 + Math.random() * 0.3, i % 3 ? '#ffab40' : '#fff3e0', 1.6);
    }
    for (let i = 0; i < 6; i++) {
      this._debris(x, y, a + (Math.random() - 0.5) * 2.2,
        90 + Math.random() * 230, 0.5 + Math.random() * 0.3, '#8d6e63');
    }
    this._smoke(x, y, Math.cos(a) * 62, Math.sin(a) * 62, 0.7, 8, '120,116,110');
    this.shake = Math.max(this.shake, 4);
    this._flashAt(0.2, '255,200,150');
    if (dmg != null && dmg > 0) {
      this._text(x, y - 16, `−${Number(dmg).toFixed(1)}`, '#ff8a65', 14, 0.8);
    }
  }

  /** Удар о стену: пыль и искры. Отскок — ярче, разбитый снаряд — глуше. */
  wall(x, y, bounce) {
    this._core(x, y, 10, 0.09, '255,236,180');
    this._ring(x, y, 3, 150, 0.26, 'rgba(200,205,215,0.7)', 2);
    const n = bounce ? 16 : 9;
    for (let i = 0; i < n; i++) {
      this._spark(x, y, Math.random() * 6.283,
        120 + Math.random() * (bounce ? 380 : 220),
        0.22 + Math.random() * 0.3, bounce && i % 3 ? '#ffe082' : '#cfd8dc', 1.5);
    }
    this._smoke(x, y, 0, -22, 0.5, 7, '140,138,132');
    if (bounce) {
      this.shake = Math.max(this.shake, 2);
      this._flashAt(0.1, '255,236,170');
    }
  }

  /** Таран: ударная волна и искры из-под корпусов. */
  ram(x, y) {
    this._core(x, y, 24, 0.16, '255,220,170');
    this._ring(x, y, 6, 270, 0.4, 'rgba(255,255,255,0.85)', 3);
    for (let i = 0; i < 20; i++) {
      const p = this._spark(x, y, Math.random() * 6.283,
        140 + Math.random() * 360, 0.3 + Math.random() * 0.3,
        i % 2 ? '#ffcc80' : '#ffffff', 1.7);
      p.g = 380;
    }
    this.shake = Math.max(this.shake, 7);
    this._flashAt(0.28, '255,225,190');
  }

  /** Уничтожение: вспышка, ударная волна, огонь, дым, обломки и тряска. */
  explosion(x, y) {
    this._core(x, y, 34, 0.2, '255,236,190');
    this._ring(x, y, 8, 330, 0.5, 'rgba(255,240,200,0.95)', 3.5);
    this._ring(x, y, 4, 200, 0.85, 'rgba(255,140,70,0.55)', 2);
    for (let i = 0; i < 34; i++) {
      const a = Math.random() * 6.283;
      const sp = 70 + Math.random() * 340;
      this._fire(x, y, Math.cos(a) * sp, Math.sin(a) * sp,
        0.4 + Math.random() * 0.5, 6 + Math.random() * 12,
        i % 3 ? '255,140,40' : '255,210,90');
    }
    for (let i = 0; i < 16; i++) {
      const a = Math.random() * 6.283;
      const sp = 30 + Math.random() * 170;
      this._smoke(x, y, Math.cos(a) * sp, Math.sin(a) * sp,
        0.9 + Math.random() * 0.8, 10 + Math.random() * 14, '80,76,74');
    }
    for (let i = 0; i < 14; i++) {
      this._debris(x, y, Math.random() * 6.283, 140 + Math.random() * 380,
        0.7 + Math.random() * 0.5, i % 3 ? '#37474f' : '#4e342e');
    }
    this.shake = Math.max(this.shake, 11);
    this._flashAt(0.45, '255,225,180');
    this._text(x, y - 36, 'УНИЧТОЖЕН', '#ff5252', 22, 1.1);
  }

  // --- шаг и отрисовка -------------------------------------------------------

  advance() {
    const now = performance.now() / 1000;
    if (!this._last) { this._last = now; return; }
    let dt = now - this._last;
    this._last = now;
    if (dt <= 0) return;
    if (dt > 0.08) dt = 0.08;               // после сворачивания вкладки
    this.shake = Math.max(0, this.shake - dt * 22);
    this.flash = Math.max(0, this.flash - dt * 3.2);

    for (let i = this.parts.length - 1; i >= 0; i--) {
      const p = this.parts[i];
      p.life -= dt;
      if (p.life <= 0) { this.parts.splice(i, 1); continue; }
      const d = Math.exp(-(p.dr || 0) * dt);
      p.vx *= d;
      p.vy *= d;
      p.vy += (p.g || 0) * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (p.vr) p.rot += p.vr * dt;
      if (p.grow) p.r += p.grow * dt;
    }
    for (let i = this.rings.length - 1; i >= 0; i--) {
      const r = this.rings[i];
      r.life -= dt;
      if (r.life <= 0) { this.rings.splice(i, 1); continue; }
      r.r += r.vr * dt;
    }
    for (let i = this.texts.length - 1; i >= 0; i--) {
      const t = this.texts[i];
      t.life -= dt;
      if (t.life <= 0) { this.texts.splice(i, 1); continue; }
      t.y += t.vy * dt;
      t.vy *= Math.exp(-1.5 * dt);
    }
  }

  draw(c) {
    if (!this.busy) return;
    const alive = (p) => Math.max(0, p.life / p.max);

    // Дым и обломки — обычное наложение: их должно быть видно поверх танка.
    for (const p of this.parts) {
      if (p.k === 'm') {
        const a = alive(p);
        c.globalAlpha = 0.32 * a * a;
        c.fillStyle = `rgba(${p.col},1)`;
        c.beginPath();
        c.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        c.fill();
      } else if (p.k === 'd') {
        c.save();
        c.globalAlpha = Math.min(1, alive(p) * 1.6);
        c.translate(p.x, p.y);
        c.rotate(p.rot || 0);
        c.fillStyle = p.col;
        c.fillRect(-p.r, -p.r * 0.7, p.r * 2, p.r * 1.4);
        c.fillStyle = 'rgba(255,255,255,0.25)';
        c.fillRect(-p.r, -p.r * 0.7, p.r * 2, 1);
        c.restore();
      }
    }
    c.globalAlpha = 1;

    // Искры и огонь — аддитивно, отсюда и берётся свечение.
    c.save();
    c.globalCompositeOperation = 'lighter';
    c.lineCap = 'round';
    for (const p of this.parts) {
      const a = alive(p);
      if (p.k === 's') {
        c.globalAlpha = a;
        c.strokeStyle = p.col;
        c.lineWidth = p.w;
        c.beginPath();
        c.moveTo(p.x, p.y);
        c.lineTo(p.x - p.vx * 0.02, p.y - p.vy * 0.02);
        c.stroke();
      } else if (p.k === 'f') {
        c.globalAlpha = a * 0.9;
        c.fillStyle = `rgba(${p.col},1)`;
        c.beginPath();
        c.arc(p.x, p.y, p.r * (0.5 + 0.5 * a), 0, Math.PI * 2);
        c.fill();
      } else if (p.k === 'c') {
        c.globalAlpha = a;
        c.fillStyle = `rgba(${p.col},1)`;
        c.beginPath();
        c.arc(p.x, p.y, p.r * (0.6 + 0.6 * (1 - a)), 0, Math.PI * 2);
        c.fill();
      }
    }
    for (const r of this.rings) {
      const a = Math.max(0, r.life / r.max);
      c.globalAlpha = a * a;
      c.strokeStyle = r.col;
      c.lineWidth = r.w * a + 0.5;
      c.beginPath();
      c.arc(r.x, r.y, r.r, 0, Math.PI * 2);
      c.stroke();
    }
    c.restore();
    c.globalAlpha = 1;

    // Надписи: тёмная обводка, чтобы читались поверх взрыва.
    c.save();
    c.textAlign = 'center';
    c.textBaseline = 'middle';
    for (const t of this.texts) {
      c.globalAlpha = Math.min(1, (t.life / t.max) * 2.2);
      c.font = `700 ${t.size}px "Segoe UI", system-ui, sans-serif`;
      c.lineWidth = 3;
      c.strokeStyle = 'rgba(0,0,0,0.7)';
      c.strokeText(t.text, t.x, t.y);
      c.fillStyle = t.col;
      c.fillText(t.text, t.x, t.y);
    }
    c.restore();
    c.globalAlpha = 1;
  }

  /** Вспышка на весь экран поверх сцены: дорисовывается уже без тряски. */
  flashDraw(c, w, h) {
    if (this.flash <= 0.01) return;
    c.save();
    c.globalCompositeOperation = 'lighter';
    c.fillStyle = `rgba(${this.flashRGB},${(this.flash * 0.4).toFixed(3)})`;
    c.fillRect(0, 0, w, h);
    c.restore();
  }
}

/**
 * Событие боя превращается в эффект: выстрел, рикошет, пробитие, стену,
 * таран, уничтожение.
 *
 * ``posOf(i)`` возвращает {x, y} танка ``i`` или null — без позиций направление
 * удара не посчитать, и эффект рисуется симметричным (это не ломает картинку,
 * просто искры летят веером, а не пучком).
 */
function fxEvent(fx, ev, posOf) {
  if (!fx || !ev || !ev.kind) return;
  const at = (i) => (posOf ? posOf(i) : null);
  switch (ev.kind) {
    case 'shot': {
      if (ev.x == null || ev.y == null) break;
      fx.muzzle(ev.x, ev.y, ev.angle != null ? ev.angle : null);
      break;
    }
    case 'hit': {
      if (ev.x == null || ev.y == null) break;
      const x = ev.x, y = ev.y;
      const src = at(ev.shooter != null ? ev.shooter : ev.tank);
      const tgt = at(ev.target);
      let ang = src ? Math.atan2(y - src.y, x - src.x) : null;
      if (ev.ricochet) {
        // Отражённое направление: нормаль грани — из центра корпуса в точку удара.
        let out = ang;
        if (tgt && ang != null) {
          const nx = x - tgt.x, ny = y - tgt.y;
          const len = Math.hypot(nx, ny) || 1;
          const ux = nx / len, uy = ny / len;
          const dot = Math.cos(ang) * ux + Math.sin(ang) * uy;
          out = Math.atan2(Math.sin(ang) - 2 * dot * uy, Math.cos(ang) - 2 * dot * ux);
        }
        fx.ricochet(x, y, out);
      } else {
        fx.impact(x, y, ang, ev.damage);
      }
      break;
    }
    case 'spark': {
      if (ev.x == null || ev.y == null) break;
      fx.wall(ev.x, ev.y, !!ev.bounce);
      break;
    }
    case 'ram': {
      if (ev.x == null || ev.y == null) break;
      fx.ram(ev.x, ev.y);
      break;
    }
    case 'death': {
      const p = at(ev.tank);
      if (!p) break;
      fx.explosion(p.x, p.y);
      break;
    }
  }
}

class ArenaView {
  /** Готовит слой карты один раз: карта не меняется от кадра к кадру. */
  constructor(canvas, map) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.map = map;
    this.tile = map.tile || 32;
    this.rows = map.rows || [];
    this.w = (map.pixel_width) || this.rows[0].length * this.tile;
    this.h = (map.pixel_height) || this.rows.length * this.tile;
    
    this.bg = document.createElement('canvas');
    this.bg.width = this.w;
    this.bg.height = this.h;
    
    this.fg = document.createElement('canvas');
    this.fg.width = this.w;
    this.fg.height = this.h;
    this._drawMap();
  }

  _drawMap() {
    const g = this.bg.getContext('2d');
    const fg = this.fg.getContext('2d');
    const t = this.tile;
    g.fillStyle = T.floor;
    g.fillRect(0, 0, this.w, this.h);
    for (let y = 0; y < this.rows.length; y++) {
      const row = this.rows[y];
      for (let x = 0; x < row.length; x++) {
        const ch = row[x];
        if (ch === '.' || ch === ' ') continue;
        const px = x * t, py = y * t;
        const base = CHAR[ch] || T.wall;
        
        const target = (ch === ',') ? g : fg;
        
        target.fillStyle = base;
        target.fillRect(px, py, t, t);
        // объём: светлая кромка сверху и тёмная снизу
        target.fillStyle = 'rgba(255,255,255,0.09)';
        target.fillRect(px, py, t, 3);
        target.fillStyle = 'rgba(0,0,0,0.25)';
        target.fillRect(px, py + t - 3, t, 3);
        if (ch === '#') {
          target.strokeStyle = 'rgba(0,0,0,0.35)';
          target.strokeRect(px + 0.5, py + 0.5, t - 1, t - 1);
        } else if (ch === 'o') {
          target.fillStyle = 'rgba(0,0,0,0.2)';
          target.beginPath();
          target.arc(px + t / 2, py + t / 2, t * 0.16, 0, Math.PI * 2);
          target.fill();
        }
      }
    }
  }

  fit() {
    // На весь экран поле растягивается под окно, а не под ширину колонки:
    // иначе картинка остаётся в углу, а вокруг неё чёрный пустой прямоугольник.
    const isFS = !!document.fullscreenElement && document.fullscreenElement.contains(this.canvas);
    if (isFS) {
      const stage = this.canvas.closest('.stage');
      let siblingsH = 0;
      for (const ch of stage.children) {
        if (ch === this.canvas) continue;
        const style = window.getComputedStyle(ch);
        if (style.display === 'none') continue;
        siblingsH += ch.offsetHeight + (parseFloat(style.marginTop) || 0)
                     + (parseFloat(style.marginBottom) || 0);
      }
      const stageStyle = window.getComputedStyle(stage);
      const padW = (parseFloat(stageStyle.paddingLeft) || 0)
                   + (parseFloat(stageStyle.paddingRight) || 0);
      const padH = (parseFloat(stageStyle.paddingTop) || 0)
                   + (parseFloat(stageStyle.paddingBottom) || 0);
      const availW = window.innerWidth - padW;
      const availH = window.innerHeight - padH - siblingsH;
      const fsScale = Math.min(availW / this.w, availH / this.h);
      this.canvas.style.width = `${Math.floor(this.w * fsScale)}px`;
      this.canvas.style.height = `${Math.floor(this.h * fsScale)}px`;
      this.canvas.style.margin = 'auto';
    } else {
      this.canvas.style.width = '100%';
      this.canvas.style.height = 'auto';
      this.canvas.style.margin = '0';
    }

    const cssW = this.canvas.clientWidth || this.w;
    const scale = cssW / this.w;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(this.w * scale * dpr);
    this.canvas.height = Math.round(this.h * scale * dpr);
    this.ctx.setTransform(scale * dpr, 0, 0, scale * dpr, 0, 0);
    this.scale = scale * dpr;
  }

  /** scene: {t, a, b, sh:[x,y,dx,dy,owner]} */
  draw(scene, opts = {}) {
    if (!scene) return;
    const c = this.ctx;
    const fx = opts.fx;
    if (fx) fx.advance();
    c.clearRect(0, 0, this.w, this.h);

    // Тряска от взрывов: сдвигаем всю сцену, а фон рисуем с запасом по краям,
    // иначе по краям поля проступила бы пустота.
    const shaking = fx && fx.shake > 0.3;
    if (shaking) {
      const amp = Math.ceil(fx.shake / 2) + 1;
      c.save();
      c.translate((Math.random() - 0.5) * fx.shake, (Math.random() - 0.5) * fx.shake);
      // Запас по краям заливаем тем же цветом, что и стены по периметру карты:
      // растягивать сам фон нельзя — стены поехали бы, а танки нет.
      c.fillStyle = T.wall;
      c.fillRect(-amp, -amp, this.w + amp * 2, this.h + amp * 2);
      c.drawImage(this.bg, 0, 0);
    } else {
      c.drawImage(this.bg, 0, 0);
    }

    if (opts.grid) {
      c.strokeStyle = T.grid;
      c.lineWidth = 1;
      for (let x = 0; x <= this.w; x += this.tile * 4) {
        c.beginPath(); c.moveTo(x, 0); c.lineTo(x, this.h); c.stroke();
      }
      for (let y = 0; y <= this.h; y += this.tile * 4) {
        c.beginPath(); c.moveTo(0, y); c.lineTo(this.w, y); c.stroke();
      }
    }

    const isPovB = opts.pov === 'b' || opts.pov === scene.b;
    const pov = isPovB ? scene.b : scene.a;
    const foeSeen = this.foeVisible(scene, opts, pov);
    for (const s of (scene.spawns || [])) this.spawnMark(s);
    if (opts.vision) this.drawVision(pov, opts);
    // Сектор обзора противника рисуем по галочке: он показывает, где вас
    // ждёт засада и почему вас вдруг перестали видеть.
    if (opts.foeVision) this.drawVision(opts.foe || (isPovB ? scene.a : scene.b), opts, true);
    
    // Стены и объекты отрисовываем поверх теней (конуса обзора)
    c.drawImage(this.fg, 0, 0);

    if (opts.trails) this.drawTrails(opts.trails, opts.idx);
    for (const b of (scene.sh || [])) this.shell(b);
    
    const aVisible = isPovB ? foeSeen : true;
    const bVisible = isPovB ? true : foeSeen;

    this.tank(scene.b, 'b', scene, bVisible);
    this.tank(scene.a, 'a', scene, aVisible);

    if (fx) fx.draw(c);
    if (shaking) c.restore();
    if (fx) fx.flashDraw(c, this.w, this.h);
  }

  /** Виден ли противник выбранному танку: в режиме зрения — только если тот его видит. */
  foeVisible(scene, opts, pov) {
    // Галочка «видеть противника» отключает секретность: положение врага
    // известно всегда.
    if (opts.reveal) return true;
    if (!opts.vision || !pov) return true;
    const mark = pov.vis !== undefined ? pov.vis : scene.enemy_seen;
    return mark === undefined ? true : !!mark;
  }

  spawnMark(s) {
    const c = this.ctx;
    c.strokeStyle = 'rgba(255,255,255,0.16)';
    c.lineWidth = 2;
    c.beginPath();
    c.arc(s.x, s.y, this.tile * 0.9, 0, Math.PI * 2);
    c.stroke();
    c.save();
    c.translate(s.x, s.y);
    c.rotate(s.angle || 0);
    c.strokeStyle = 'rgba(255,255,255,0.3)';
    c.beginPath();
    c.moveTo(0, 0);
    c.lineTo(this.tile * 1.4, 0);
    c.stroke();
    c.restore();
  }

  /**
   * Траектория выстрела: только то, что уже произошло, и не дольше TTL.
   *
   * Раньше рисовался весь маршрут каждого снаряда боя сразу и навсегда —
   * карта забивалась линиями из будущего. Теперь трек обрезается по текущему
   * кадру (`pts[i][2]` — тик), а хвост гаснет за 0.8 с после попадания.
   */
  drawTrails(trails, idx) {
    if (!trails || !trails.length) return;
    const c = this.ctx;
    const at = idx === undefined ? Infinity : idx;
    for (const tr of trails) {
      const pts = tr.pts || [];
      if (!pts.length || pts[pts.length - 1][2] > at) continue;
      let n = 0;
      while (n < pts.length && pts[n][2] <= at) n++;
      if (n < 2) continue;                       // выстрел только что был
      const age = (at - pts[n - 1][2]) / TRAIL_TTL;
      if (age >= 1) continue;                    // хвост погас
      c.strokeStyle = tr.o === 0 ? 'rgba(255,112,67,0.65)' : 'rgba(62,166,255,0.65)';
      c.globalAlpha = 0.15 + 0.85 * (1 - age);
      c.lineWidth = 1 + 2 * (1 - age);
      c.lineCap = 'round';
      c.beginPath();
      c.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < n; i++) c.lineTo(pts[i][0], pts[i][1]);
      c.stroke();
      c.globalAlpha = 1;
    }
  }

  /** Символ тайла под точкой; вне карты считаем стеной. */
  tileAt(x, y) {
    const tx = Math.floor(x / this.tile), ty = Math.floor(y / this.tile);
    const row = this.rows[ty];
    if (!row || tx < 0 || tx >= row.length) return '#';
    return row[tx];
  }

  /** Длина луча до преграды или до предела дальности. */
  rayLength(ox, oy, dx, dy, max_t) {
    if (OPAQUE.has(this.tileAt(ox, oy))) return 0;
    let x = Math.floor(ox / this.tile);
    let y = Math.floor(oy / this.tile);
    const step_x = dx > 0 ? 1 : -1;
    const step_y = dy > 0 ? 1 : -1;
    const t_delta_x = dx !== 0 ? Math.abs(this.tile / dx) : Infinity;
    const t_delta_y = dy !== 0 ? Math.abs(this.tile / dy) : Infinity;
    
    let t_max_x = Infinity;
    if (dx > 0) t_max_x = ((x + 1) * this.tile - ox) / dx;
    else if (dx < 0) t_max_x = (x * this.tile - ox) / dx;
    
    let t_max_y = Infinity;
    if (dy > 0) t_max_y = ((y + 1) * this.tile - oy) / dy;
    else if (dy < 0) t_max_y = (y * this.tile - oy) / dy;

    let t = 0;
    let guard = 0;
    const limit = (this.w + this.h) / this.tile + 4;
    while (t <= max_t && guard < limit * 4) {
      guard++;
      if (t_max_x < t_max_y) {
        t = t_max_x;
        t_max_x += t_delta_x;
        x += step_x;
      } else {
        t = t_max_y;
        t_max_y += t_delta_y;
        y += step_y;
      }
      if (t > max_t) break;
      if (OPAQUE.has(this.tileAt(x * this.tile + this.tile / 2, y * this.tile + this.tile / 2))) {
        return t;
      }
    }
    return max_t;
  }

  /**
   * Область обзора, обрезанная стенами: лучи по кругу от -half до +half.
   * cone — половина угла в градусах, 180 = полный круг.
   */
  visionPolygon(me, range, cone) {
    const full = cone >= 179.9;
    const rays = full ? 72 : 40; // kept for tests
    const half = (cone * Math.PI) / 180;
    const angles = [];
    
    angles.push(-half, half);
    
    const t = this.tile;
    const added = new Set();
    const addCorner = (x, y) => {
       const key = `${x},${y}`;
       if (added.has(key)) return;
       added.add(key);
       const px = x * t, py = y * t;
       const distSq = (px - me.x) * (px - me.x) + (py - me.y) * (py - me.y);
       if (distSq > range * range + t * t * 2) return;
       const ang = Math.atan2(py - me.y, px - me.x);
       let rel = ang - me.h;
       while (rel < -Math.PI) rel += Math.PI * 2;
       while (rel > Math.PI) rel -= Math.PI * 2;
       if (full || (rel >= -half - 0.001 && rel <= half + 0.001)) {
           angles.push(rel - 0.00001, rel, rel + 0.00001);
       }
    };

    for (let y = 0; y < this.rows.length; y++) {
      for (let x = 0; x < this.rows[y].length; x++) {
         if (OPAQUE.has(this.rows[y][x])) {
             addCorner(x, y);
             addCorner(x + 1, y);
             addCorner(x, y + 1);
             addCorner(x + 1, y + 1);
         }
      }
    }
    
    const circleSteps = full ? 36 : Math.max(10, Math.ceil(36 * cone / 180));
    for (let i = 0; i <= circleSteps; i++) {
       angles.push(-half + (half * 2) * (i / circleSteps));
    }

    angles.sort((a, b) => a - b);

    const pts = [];
    for (let i = 0; i < angles.length; i++) {
      const rel = angles[i];
      if (!full && (rel < -half || rel > half)) continue;
      if (i > 0 && rel - angles[i - 1] < 1e-7) continue;

      const ang = me.h + rel;
      const dx = Math.cos(ang), dy = Math.sin(ang);
      const d = this.rayLength(me.x, me.y, dx, dy, range);
      pts.push([me.x + dx * d, me.y + dy * d]);
    }
    return pts;
  }

  /**
   * Область обзора рисуем от выбранного танка: секретность.
   *
   * foe=true рисует чужой сектор другим оттенком — так видно, где противник
   * вас видит, а где вы для него невидимы.
   */
  drawVision(me, opts = {}, foe = false) {
    if (!me) return;
    const c = this.ctx;
    const range = opts.range || 760;
    // opts.cone — половина угла обзора, как в Balance.view_cone.
    const cone = opts.cone == null ? 180 : opts.cone;
    const pts = this.visionPolygon(me, range, cone);
    if (!pts.length) return;

    c.beginPath();
    if (cone < 179.9) {
      c.moveTo(me.x, me.y);
      for (const [x, y] of pts) c.lineTo(x, y);
    } else {
      c.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) c.lineTo(pts[i][0], pts[i][1]);
    }
    c.closePath();
    c.fillStyle = foe ? 'rgba(255,120,120,0.10)' : 'rgba(255,255,255,0.07)';
    c.fill();
    c.strokeStyle = foe ? 'rgba(255,120,120,0.35)' : 'rgba(255,255,255,0.12)';
    c.lineWidth = 1;
    c.stroke();
  }

  shell(b) {
    const c = this.ctx;
    c.fillStyle = b[4] === 0 ? '#ffb74d' : '#7fc4ff';
    c.beginPath();
    c.arc(b[0], b[1], 3.5, 0, Math.PI * 2);
    c.fill();
    c.strokeStyle = 'rgba(255,255,255,0.25)';
    c.lineWidth = 1;
    c.beginPath();
    c.moveTo(b[0], b[1]);
    c.lineTo(b[0] + b[2] * 22, b[1] + b[3] * 22);
    c.stroke();
  }

  tank(t, who, scene, visible = true) {
    if (!t || !visible) return;
    const c = this.ctx;
    const color = who === 'a' ? '#ff7043' : '#3ea6ff';
    const alive = t.alive === undefined ? 1 : t.alive;

    // корпус
    c.save();
    c.translate(t.x, t.y);
    c.rotate(t.h);
    c.globalAlpha = alive ? 1 : 0.4;
    c.fillStyle = '#20242b';
    c.strokeStyle = color;
    c.lineWidth = 2.5;
    this._roundRect(c, -17, -11, 34, 22, 5);
    c.fill();
    c.stroke();
    c.fillStyle = color;
    c.fillRect(12, -8, 6, 16);       // передний брус
    // башня
    c.rotate(t.u - t.h);
    c.fillStyle = color;
    c.beginPath();
    c.arc(0, 0, 7.5, 0, Math.PI * 2);
    c.fill();
    c.strokeStyle = '#12151a';
    c.lineWidth = 2;
    c.beginPath();
    c.moveTo(0, 0);
    c.lineTo(26, 0);
    c.stroke();
    c.restore();

    // полоска здоровья
    const bw = 34;
    const frac = Math.max(0, Math.min(1, t.hp / 10));
    c.fillStyle = 'rgba(0,0,0,0.55)';
    c.fillRect(t.x - bw / 2, t.y - 24, bw, 4);
    c.fillStyle = frac > 0.6 ? '#4caf7d' : frac > 0.3 ? '#ffb300' : '#e05c5c';
    c.fillRect(t.x - bw / 2, t.y - 24, bw * frac, 4);

    if (t.cd > 0.05) {              // готовность выстрела
      c.strokeStyle = 'rgba(255,255,255,0.35)';
      c.lineWidth = 2;
      c.beginPath();
      c.arc(t.x, t.y, 20, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (1 - t.cd));
      c.stroke();
    }
    if (t.bad) {
      c.fillStyle = '#e05c5c';
      c.font = 'bold 14px sans-serif';
      c.fillText('!', t.x + 14, t.y - 14);
    }
  }

  _roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }
}

/** Кадр реплея собираем из колонок: t, a-танк, b-танк, живые снаряды. */
class ReplayPlayer {
  constructor(data) {
    this.data = data;
    this.t = data.t;
    this.shots = data.shots || null;      // {i: [[t, [x,y,dx,dy,owner]]]}
    this.trails = data.bullets || [];
    this.n = this.t.length;
    this.i = 0;
    this.playing = false;
    this.speed = 1;
    this.events = data.events || [];
  }

  get duration() { return this.t.length ? this.t[this.n - 1] : 0; }

  frameAt(idx) {
    const d = this.data;
    const i = Math.max(0, Math.min(this.n - 1, Math.round(idx)));
    const t = this.t[i];
    const frame = { t, a: {}, b: {}, sh: [], spawns: (d.map && d.map.spawns) || null };
    for (const k of Object.keys(d.a)) {
      frame.a[k] = d.a[k][i];
      frame.b[k] = d.b[k][i];
    }
    const s = this.shots && this.shots.get(i);
    if (s) frame.sh = s;
    return frame;
  }

  eventsUpTo(t, kind) {
    const out = [];
    for (const ev of this.events) {
      if (ev[0] > t + 1e-6) break;
      if (!kind || ev[1] === kind) out.push(ev);
    }
    return out;
  }
}

window.ArenaView = ArenaView;
window.ReplayPlayer = ReplayPlayer;
window.Fx = Fx;
window.fxEvent = fxEvent;
