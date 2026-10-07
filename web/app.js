/* Логика интерфейса TANKSIM: каталог, расчёт боя, реплеи, ручной бой. */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const S = {
  catalog: null,
  programs: [],
  maps: [],
  balance: null,
  replay: null,          // ReplayPlayer
  view: null,            // ArenaView для вкладки «Бой»
  repView: null,         // ArenaView для вкладки «Реплеи»
  manView: null,
  raf: 0,
  playing: false,
  run: null,
  man: null,
  // Загруженные бойцы по слотам: два участника боя и противник ручного боя.
  // Значение — { key, name, file, meta } либо null, если поле пустое.
  slots: { a: null, b: null, foe: null },
};

// --- сеть -------------------------------------------------------------------

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.message || detail;
    } catch (e) { /* тело не json */ }
    throw new Error(detail);
  }
  return res.json();
}

function toast(msg, bad = false) {
  const n = $('toast');
  n.textContent = msg;
  n.classList.toggle('bad', bad);
  n.classList.add('show');
  clearTimeout(n._t);
  n._t = setTimeout(() => n.classList.remove('show'), 4000);
}

function fmtTime(s) {
  return `${s.toFixed(1)} с`;
}

// --- вкладки ----------------------------------------------------------------

$('tabs').addEventListener('click', (e) => {
  const b = e.target.closest('.tab');
  if (!b) return;
  for (const t of $('tabs').children) t.classList.toggle('active', t === b);
  for (const p of document.querySelectorAll('.page')) {
    p.classList.toggle('active', p.id === `page-${b.dataset.tab}`);
  }
  resizeActive();
});

function resizeActive() {
  for (const v of [S.view, S.repView, S.manView]) if (v) v.fit();
}

// --- каталог ----------------------------------------------------------------

async function loadCatalog() {
  const cat = await api('/api/catalog');
  S.catalog = cat;
  S.programs = cat.programs.filter((p) => p.ok);
  S.maps = cat.maps;
  S.balance = cat.balance;

  const mopt = (m) => {
    const o = el('option', null, `${m.name} · ${m.size}`);
    o.value = m.id;
    return o;
  };
  for (const id of ['sel-map', 'man-map']) {
    const s = $(id);
    s.innerHTML = '';
    S.maps.forEach((m) => s.appendChild(mopt(m)));
  }
  $('man-map').selectedIndex = Math.min(2, S.maps.length - 1);

  // view_cone — половина угла обзора, 180 = круг во все стороны.
  $('doc-range').textContent = cat.balance.view_cone >= 180
    ? `круг ${cat.balance.view_range} px вокруг танка`
    : `${cat.balance.view_range} px и ${cat.balance.view_cone * 2}°`;
  $('doc-ram').textContent =
    `${cat.balance.ram_damage} HP при сближении на ${cat.balance.ram_speed} px/с, не чаще раза в ${cat.balance.ram_cooldown} с`;
  fillArmor();
  renderSlot('a');
  renderSlot('b');
  renderSlot('foe');
}

async function fillArmor() {
  const bal = await api('/api/balance');
  const t = $('doc-armor');
  t.innerHTML = '';
  const head = el('tr');
  ['Угол', 'Лоб', 'Борт', 'Корма'].forEach((h) => head.appendChild(el('th', null, h)));
  t.appendChild(head);
  const thetas = [...new Set(bal.armor_table.map((r) => r.theta))].sort((a, b) => a - b);
  for (const th of thetas) {
    const row = el('tr');
    row.appendChild(el('td', null, `${th}°`));
    for (const face of ['front', 'side', 'rear']) {
      const r = bal.armor_table.find((x) => x.theta === th && x.face === face);
      if (!r) { row.appendChild(el('td', 'num', '—')); continue; }
      // Урон от брони не зависит: ячейка показывает рикошет или пробитие.
      row.appendChild(el('td', r.ricochet ? 'muted' : 'num',
        r.ricochet ? 'рикошет' : `пробитие ${r.damage.toFixed(2)}`));
    }
    t.appendChild(row);
  }
}

// --- выбор бойца перетаскиванием скрипта -------------------------------------

const DIFFICULTY = ['', 'простой', 'средний', 'сложный', 'эксперт', 'мастер'];

/** Имя боца из имени файла: всё, кроме .py, .zip и служебного .tankp. */
function fighterName(fileName) {
  return (fileName || '').replace(/\.py$/i, '').replace(/\.zip$/i, '')
    .replace(/\.tankp$/i, '') || 'program';
}

/** Программа — это скрипт .py или zip с main.py внутри. */
function isProgramFile(fileName) {
  return /\.(py|zip)$/i.test(fileName || '');
}

/** Загружает скрипт на сервер и превращает ответ в запись для слота. */
async function uploadFighter(file) {
  const fd = new FormData();
  fd.append('file', file);
  const meta = await api('/api/upload', { method: 'POST', body: fd });
  return {
    key: meta.key,
    name: meta.fighter || fighterName(file.name),
    file: meta.file || file.name,
    meta,
  };
}

function renderSlot(slot) {
  const zone = $(`drop-${slot}`);
  const data = S.slots[slot];
  zone.classList.toggle('filled', !!data);
  zone.querySelector('.drop-empty').hidden = !!data;
  zone.querySelector('.drop-full').hidden = !data;
  if (data) {
    zone.querySelector('.drop-name').textContent = data.name;
    zone.querySelector('.drop-file').textContent = data.file;
  }
  renderSlotMeta(slot, data);
}

/** Подпись под полем: сложность, описание и предупреждения разбора скрипта. */
function renderSlotMeta(slot, data) {
  const box = $(`meta-${slot}`);
  box.innerHTML = '';
  if (!data) { box.textContent = '—'; return; }
  const p = data.meta || {};
  const diff = DIFFICULTY[p.difficulty] || '';
  const parts = [diff, p.description].filter(Boolean);
  box.appendChild(el('div', null, parts.join(' · ') || '—'));
  if (p.warnings && p.warnings.length) {
    box.appendChild(el('div', 'bad', `предупреждения: ${p.warnings.join('; ')}`));
  }
}

function setSlot(slot, data) {
  S.slots[slot] = data;
  renderSlot(slot);
  if (slot === 'a' || slot === 'b') checkSameFighter();
  if (slot === 'a') showSource(data ? data.key : null);
}

/** Один и тот же боец в двух полях — бой не запустится, предупреждаем сразу. */
function checkSameFighter() {
  const { a, b } = S.slots;
  $('btn-fight').classList.toggle('warn', !!(a && b && a.key === b.key));
}

function wireSlot(slot) {
  const zone = $(`drop-${slot}`);
  const input = $(`file-${slot}`);

  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    zone.classList.add('over');
  });
  zone.addEventListener('dragleave', (e) => {
    if (!zone.contains(e.relatedTarget)) zone.classList.remove('over');
  });
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('over');
    take(e.dataTransfer.files[0]);
  });
  zone.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-act]');
    if (btn) {
      if (btn.dataset.act === 'clear') setSlot(slot, null);
      else input.click();
      return;
    }
    input.click();
  });
  zone.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    input.click();
  });
  input.addEventListener('change', () => {
    take(input.files[0]);
    input.value = '';
  });

  async function take(file) {
    if (!file) return;
    if (!isProgramFile(file.name)) {
      toast(`«${file.name}» — это не скрипт и не zip с программой`, true);
      return;
    }
    zone.classList.add('busy');
    try {
      const data = await uploadFighter(file);
      setSlot(slot, data);
      // Каталог подтягиваем после загрузки: новый боец нужен и в списках
      // участников. Ошибка тут не важна — поле уже заполнено.
      loadCatalog().catch(() => {});
      toast(`боец ${data.name} загружен`);
    } catch (err) {
      toast(`не загрузилось: ${err.message}`, true);
    } finally {
      zone.classList.remove('busy');
    }
  }
}

for (const slot of ['a', 'b', 'foe']) wireSlot(slot);

async function showSource(key) {
  if (!key) { $('src-view').textContent = 'перетащите скрипт в поле «Танк A»'; return; }
  try {
    const d = await api(`/api/programs/source?key=${encodeURIComponent(key)}`);
    $('src-view').textContent = d.source;
  } catch (e) {
    $('src-view').textContent = `не удалось прочитать исходник: ${e.message}`;
  }
}

// --- расчёт боя -------------------------------------------------------------

$('btn-fight').addEventListener('click', async () => {
  const a = S.slots.a;
  const b = S.slots.b;
  if (!a || !b) {
    toast('перетащите Python скрипт ИИ бойца в оба поля', true);
    return;
  }
  if (a.key === b.key) {
    toast('нельзя выставить бойца против самого себя', true);
    return;
  }
  const body = {
    a: { kind: 'script', key: a.key, name: a.name },
    b: { kind: 'script', key: b.key, name: b.name },
    map_name: $('sel-map').value,
    seed: Number($('in-seed').value) || 0,
    budget_ms: Number($('in-budget').value) || 10,
  };
  $('btn-fight').disabled = true;
  $('sim-log').innerHTML = '';
  setProgress('sim-progress', 0, 'запуск');
  try {
    const r = await api('/api/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    S.run = r.run_id;
    pollRun(r.run_id);
  } catch (e) {
    toast(`бой не запустился: ${e.message}`, true);
    $('btn-fight').disabled = false;
  }
});

async function pollRun(id) {
  try {
    const st = await api(`/api/simulate/${id}`);
    setProgress('sim-progress', st.progress, st.message || st.status);
    if (st.status === 'done') {
      setProgress('sim-progress', 1, 'бой рассчитан');
      $('btn-fight').disabled = false;
      const rep = await api(`/api/replay/${st.replay_id || id}`);
      await showReplay(rep, 'sim');
      return;
    }
    if (st.status === 'error' || st.status === 'failed') {
      $('btn-fight').disabled = false;
      setProgress('sim-progress', 0, 'ошибка');
      toast(st.error || 'расчёт не удался', true);
      return;
    }
    setTimeout(() => pollRun(id), 220);
  } catch (e) {
    $('btn-fight').disabled = false;
    toast(e.message, true);
  }
}

function setProgress(which, frac, text) {
  const n = $(which);
  if (!n) return;
  n.querySelector('i').style.width = `${Math.round(Math.max(0, Math.min(1, frac)) * 100)}%`;
  n.querySelector('span').textContent = text;
}

// --- проигрывание реплея ----------------------------------------------------

function shotsMap(payload) {
  const m = new Map();
  for (const [k, v] of Object.entries(payload.shots || {})) m.set(Number(k), v);
  return m;
}

function mkPlayer(payload) {
  const p = new ReplayPlayer(payload);
  p.shots = shotsMap(payload);
  return p;
}

async function showReplay(payload, where) {
  const player = mkPlayer(payload);
  const meta = payload.meta || {};
  const names = [meta.a && meta.a.name, meta.b && meta.b.name];
  const name = names.filter(Boolean).join('  —  ') || 'бой';
  const map = payload.map || {};

  // Итог не показываем заранее: он появляется только когда бой доигран,
  // иначе результат выдаётся до того, как зрителю показали последний взрыв.
  const showSummary = (box) => renderSummary($(box), payload.summary, names);

  if (where === 'sim') {
    S.replay = player;
    S.view = new ArenaView($('cv-sim'), map);
    S.view.fit();
    $('sim-title').textContent = `${name} · ${map.name || ''}`;
    $('sim-result').textContent = 'бой идёт…';
    renderLog($('sim-log'), payload.events || []);
    attachPlay($('sim-play'), $('sim-seek'), $('sim-time'), $('sim-speed'),
      () => S.replay, S.view, 'sim');
    startPlayback(() => S.view, () => S.replay, 'sim',
      () => showSummary('sim-result'));
  } else {
    S.repView = new ArenaView($('cv-rep'), map);
    S.repView.fit();
    S.repReplay = player;
    $('rep-title').textContent = `${name} · ${map.name || ''}`;
    $('rep-result').textContent = 'бой идёт…';
    renderLog($('rep-log'), payload.events || []);
    attachPlay($('rep-play'), $('rep-seek'), $('rep-time'), $('rep-speed'),
      () => S.repReplay, S.repView, 'rep');
    startPlayback(() => S.repView, () => S.repReplay, 'rep',
      () => showSummary('rep-result'));
  }
}

function renderSummary(box, sum, names) {
  box.innerHTML = '';

  if (!sum || !sum.outcome) { box.textContent = '\u2014'; return; }
  const head = el('div');
const label = { a_win: names[0] + ' победил', b_win: names[1] + ' победил',
    draw: 'ничья', interrupted: 'бой прерван' }[sum.outcome] || sum.outcome;
    head.className = sum.outcome === 'draw' ? 'draw' : 'win';
  head.textContent = label;
  box.appendChild(head);
  const tail = sum.duration ? ' \u00b7 ' + fmtTime(sum.duration) : '';
  box.appendChild(el('div', 'draw', (sum.reason || '') + tail));
  for (const side of ['a', 'b']) {
    const st = sum[side];
    if (!st) continue;
    const n = names[side] || side.toUpperCase();
    const acc = st.shots ? ' \u00b7 точность ' + Math.round(st.hits / st.shots * 100) + '%' : '';
    const think = st.think_ms_max ? ' \u00b7 решение ' + st.think_ms_max.toFixed(2) + ' мс' : '';
    const bad = (st.over_budget || 0) + (st.errors || 0) + (st.timeouts || 0);
    const viol = bad ? ' \u00b7 нарушений ' + bad : '';
    box.appendChild(el('div', null, n + ': ХП ' + (st.hp_left != null ? st.hp_left : '\u2014') +
      ' \u00b7 выстрелов ' + (st.shots || 0) + ' \u00b7 попаданий ' + (st.hits || 0) + acc + think + viol));
  }
}

const KIND_CLASS = { shot: '', hit: 'hit', death: 'dead', end: 'sys', ram: 'hit',
  log: 'sys', start: 'sys', spark: 'hit', barrel_blocked: 'sys', error: 'dead' };

const FACE = { front: 'лоб', side: 'борт', rear: 'корма' };

/**
 * Порядок полей в строке события реплея: ровно столько же ключей отдаёт
 * engine/replay.py, и строка всегда одной длины. Раньше ключи добавлялись
 * только те, что были у события, — колонки съезжали, и «попадание в»
 * показывало урон, а рикошет читался как имя танка.
 */
const EV_KEYS = ['tank', 'target', 'shooter', 'damage', 'face', 'theta',
  'ricochet', 'bounce', 'outcome', 'reason', 'x', 'y', 'angle'];

/** Строка события реплея превращается в объект: с ним удобнее работать. */
function toEvent(row) {
  if (!Array.isArray(row)) return row;
  if (row.length === EV_KEYS.length + 2) {
    const ev = { t: row[0], kind: row[1] };
    EV_KEYS.forEach((k, i) => { ev[k] = row[2 + i]; });
    return ev;
  }
  // Старый формат: сообщение из log() шло короткой строкой [t, танк, текст].
  if (typeof row[1] === 'number') {
    return { t: row[0], kind: 'log', tank: row[1], reason: row[2] };
  }
  const [t, kind, tank, target, damage, face, theta, ricochet, outcome, reason] = row;
  return { t, kind, tank, target, damage, face, theta, ricochet, outcome, reason };
}

/** Имя стороны по номеру танка: 0 — A, 1 — B, иначе пусто. */
function sideName(i) {
  return i === 0 || i === '0' ? 'A' : (i === 1 || i === '1' ? 'B' : '');
}

function describeEvent(ev) {
  const who = sideName(ev.tank);
  const hitWho = sideName(ev.target != null ? ev.target : ev.tank);
  const face = FACE[ev.face] ? ' (' + FACE[ev.face] + ')' : '';
  switch (ev.kind) {
    case 'start': return 'бой начался';
    case 'end': return 'бой окончен: ' + (ev.outcome || '') + ' (' + (ev.reason || '') + ')';
    case 'shot': return who + ' выстрелил';
    case 'hit': return 'попадание в ' + hitWho + ': ' + ev.damage + ' урона' + face +
      (ev.ricochet ? ', рикошет' : '');
    case 'ram': return (who || hitWho || '?') + ' таран: ' + ev.damage + ' урона';
    case 'death': return who + ' уничтожен';
    case 'spark': return ev.bounce === false
      ? 'снаряд разбился о стену' : 'рикошет от стены';
    case 'barrel_blocked': return who + ': ствол упёрся в стену';
    case 'log': return who + ': ' + (ev.reason || '');
    case 'error': return 'ошибка ' + who + ': ' + (ev.reason || '');
    default: return ev.kind;
  }
}

function renderLog(box, events) {
  box.innerHTML = '';
  for (const row of events) {
    const ev = toEvent(row);
    const line = el('div', 'row');
    line.appendChild(el('span', 't', (ev.t || 0).toFixed(1)));
    line.appendChild(el('span', KIND_CLASS[ev.kind] || '', describeEvent(ev)));
    box.appendChild(line);
  }
  box.scrollTop = box.scrollHeight;
}

function attachPlay(btn, seek, time, speed, getPlayer, view, which) {
  btn.onclick = () => {
    const p = getPlayer();
    if (!p) return;
    p.playing = !p.playing;
    if (p.playing && p.i >= p.n - 1) p.i = 0;
    btn.textContent = p.playing ? '❚❚' : '▶';
  };
  seek.oninput = () => {
    const p = getPlayer();
    if (!p) return;
    p.i = (seek.value / 1000) * (p.n - 1);
    p.playing = false;
    btn.textContent = '▶';
  };
  speed.onchange = () => {
    const p = getPlayer();
    if (p) p.speed = Number(speed.value);
  };
}

// Одинаковый плеер для симуляции и просмотра реплея. Идентификаторы
// галок и кнопок лежат здесь, чтобы не расползались по двум местам.
const PLAY_UI = {
  sim: {
    page: 'page-sim', pov: 'sim-pov', vision: 'opt-vision', trails: 'opt-trails', grid: 'opt-grid',
    btn: 'sim-play', seek: 'sim-seek', time: 'sim-time',
  },
  rep: {
    page: 'page-replays', pov: 'rep-pov', vision: 'rep-vision', trails: 'rep-trails', grid: null,
    btn: 'rep-play', seek: 'rep-seek', time: 'rep-time',
  },
};

// --- эффекты боя: события превращаются в частицы ----------------------------

/** Время события: у строки реплея оно первым элементом, у живого — поле t. */
function evTime(row) {
  return Array.isArray(row) ? row[0] : (row && row.t) || 0;
}

/** Позиции танков из кадра реплея — чтобы привязать эффект к точке карты. */
function framePosOf(frame) {
  return (i) => {
    const t = Number(i) === 0 ? frame.a : (Number(i) === 1 ? frame.b : null);
    return t && t.x !== undefined ? { x: t.x, y: t.y } : null;
  };
}

/**
 * Уничтожался ли кто-то в этом бою: после гибели танка концовку показываем
 * с паузой, чтобы зритель успел посмотреть на взрыв.
 */
function replayLinger(p) {
  if (p.linger === undefined) {
    p.linger = (p.events || []).some(
      (r) => (Array.isArray(r) ? r[1] : (r && r.kind)) === 'death');
  }
  return p.linger;
}

/**
 * События реплея, попавшие в только что проигранный кусок, — в эффекты.
 *
 * Перемотка вперёд или назад гасит накопившиеся частицы и курсор событий:
 * искры из середины боя не должны сыпаться поверх кадра, куда зритель
 * перетащил ползунок.
 */
function fxReplay(p, from, to, frame) {
  const fx = p.fx || (p.fx = new Fx());
  const T = p.data.t;
  const at = (i) => T[Math.min(p.n - 1, Math.max(0, Math.round(i)))];
  const t0 = at(from);
  const t1 = at(to);
  // Нормальная скорость проигрывания тянет максимум на 0.4 с за кадр (4×),
  // так что большой шаг — это всегда перемотка, а не спешащий зритель.
  const maxGap = 0.45 * (p.speed || 1) + 0.2;
  if (to < from - 0.001 || t1 - t0 > maxGap) {
    fx.clear();
    p.evi = 0;
    return fx;
  }
  if (!(t1 > t0)) return fx;
  if (p.evi === undefined) p.evi = 0;
  const evs = p.events || [];
  const pos = framePosOf(frame);
  let i = p.evi;
  while (i < evs.length && evTime(evs[i]) <= t0) i++;
  while (i < evs.length && evTime(evs[i]) <= t1) {
    fxEvent(fx, toEvent(evs[i]), pos);
    i++;
  }
  p.evi = i;
  return fx;
}

/**
 * Эффекты живого боя: события кадров, по которым уже прошли часы интерполяции.
 *
 * События приходят пачками по 8 кадров, поэтому копим их и выпускаем, когда
 * часы догоняют кадр — иначе искры выстрела появлялись бы раньше выстрела.
 */
function fxLive(sess, clockT, side, scene) {
  const fx = sess.fx || (sess.fx = new Fx());
  if (sess.evtT === undefined) sess.evtT = 0;
  // Часы прыгнули назад (новый бой) или очень далеко вперёд — чистим.
  if (clockT < sess.evtT - 0.01 || clockT - sess.evtT > 3) {
    sess.evtT = 0;
    fx.clear();
    sess.deathSeen = false;
  }
  const mine = side === 'b' ? 1 : 0;
  const pos = (i) => {
    const t = Number(i) === mine ? scene.me : scene.enemy;
    return t && t.x !== undefined ? { x: t.x, y: t.y } : null;
  };
  for (const f of sess.frames) {
    if (f.t <= sess.evtT || f.t > clockT) continue;
    for (const raw of (f.events || [])) {
      const ev = (raw && raw.kind) ? raw : toEvent(raw);
      if (ev.kind === 'death') sess.deathSeen = true;
      fxEvent(fx, ev, pos);
    }
  }
  sess.evtT = clockT;
  return fx;
}

function startPlayback(getView, getPlayer, which, onEnd) {
  if (S.raf) cancelAnimationFrame(S.raf);
  const ui = PLAY_UI[which] || PLAY_UI.rep;
  let last = performance.now();
  let ended = false;
  let endAt = 0;                       // когда концовка считается показанной
  const finish = () => {
    if (ended) return;
    ended = true;
    if (onEnd) onEnd();
  };
  const loop = (now) => {
    S.raf = requestAnimationFrame(loop);
    const view = getView();
    const p = getPlayer();
    if (!view || !p) return;
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    const before = p.i;
    if (p.playing) {
      p.i += dt * 60 * p.speed;
      if (p.i >= p.n - 1) {
        p.i = p.n - 1;
        p.playing = false;
      }
    }
    // После уничтожения бой ещё секунду живёт на экране: только потом
    // показываем концовку (итог, победитель, кнопка повтора).
    const atEnd = (p.i >= p.n - 1);
    const linger = replayLinger(p);
    if (!atEnd) endAt = 0;              // бой пересматривают — пауза заново
    if (atEnd && linger && !endAt) endAt = now + END_LINGER_MS;
    const done = atEnd && (!linger || now >= endAt);
    if (done) finish();
    const page = $(ui.page);
    if (page && !page.classList.contains('active')) return;

    const isEnd = done;
    const isFS = !!document.fullscreenElement;
    if (which === 'sim') {
      const overlay = $('sim-repeat');
      if (overlay) overlay.hidden = !(isEnd && isFS);
    } else if (which === 'rep') {
      const overlay = $('rep-repeat');
      if (overlay) overlay.hidden = !(isEnd && isFS);
    }
    const frame = p.frameAt(p.i);
    const fx = fxReplay(p, before, p.i, frame);
    // Галочки читаем каждый кадр: иначе переключатель не действует до
    // перезапуска просмотра. Раньше здесь считался opts.trails, а в draw()
    // уходил массив p.trails — галочка просто не работала.
    const povSel = $(ui.pov);
    const pov = povSel && povSel.value === 'b' ? 'b' : 'a';
    view.draw(frame, {
      vision: $(ui.vision).checked,
      grid: ui.grid ? $(ui.grid).checked : false,
      trails: $(ui.trails).checked ? p.trails : null,
      idx: Math.round(p.i),
      range: S.balance && S.balance.view_range,
      cone: S.balance && S.balance.view_cone,
      pov,
      fx,
    });
    const seek = $(ui.seek);
    const time = $(ui.time);
    const btn = $(ui.btn);
    if (document.activeElement !== seek) seek.value = Math.round((p.i / Math.max(1, p.n - 1)) * 1000);
    time.textContent = `${(frame.t || 0).toFixed(1)} / ${p.duration.toFixed(1)} с`;
    btn.textContent = p.playing ? '❚❚' : '▶';
  };
  S.raf = requestAnimationFrame(loop);
}

window.addEventListener('resize', resizeActive);
document.addEventListener('fullscreenchange', resizeActive);

document.querySelectorAll('.fs-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    const stage = btn.closest('.stage');
    if (!document.fullscreenElement) {
      stage.requestFullscreen().catch((err) => toast(`Не удалось: ${err.message}`, true));
    } else {
      document.exitFullscreen();
    }
  });
});

// --- ручной бой -------------------------------------------------------------

let keysDown = new Set();
let mouseDown = false;

/** Сессия, которая сейчас на экране: живёт только ручной бой. */
function gameSession() {
  return S.man;
}

/** Поле, на котором держится фокус клавиатуры. */
function gameCanvas() {
  return cvMan;
}

/** Открыт ли сейчас живой бой. */
function liveGameOpen() {
  return !!document.querySelector('#page-manual.active');
}

$('man-start').addEventListener('click', async () => {
  const foe = S.slots.foe;
  if (!foe) {
    toast('перетащите Python скрипт ИИ противника', true);
    return;
  }
  try {
    const res = await api('/api/manual/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: foe.key,
        player_side: $('man-side').value,
        map_name: $('man-map').value,
        player_name: $('man-name').value || 'Игрок',
        seed: Math.floor(Math.random() * 100000),
        budget_ms: 10,
        reveal: $('man-reveal') ? $('man-reveal').checked : false,
      }),
    });
    $('man-log').innerHTML = '';
    $('man-result').textContent = 'бой идёт…';
    $('man-stop').disabled = false;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/api/manual/ws/${res.session_id}`);
    S.man = { ws, sid: res.session_id, map: null, side: $('man-side').value,
      frames: [], aim: null, aimReady: false };
    // Фокус на поле: иначе после клика по «Начать бой» клавиши уходили
    // в поля панели (а «Имя» и выпадающие списки считаются текстовыми
    // полями) и газ с поворотом корпуса не доходили до сервера.
    for (const el of document.querySelectorAll('#page-manual select, #page-manual input')) {
      el.blur();
    }
    cvMan.focus();

    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === 'hello') {
        S.man.map = m.map;
        S.manView = new ArenaView($('cv-man'), m.map);
        S.manView.fit();
        $('man-title').textContent = `ручной бой · ${m.map.name}`;
        $('cv-man').focus();
      } else if (m.type === 'frames') {
        S.man.frames.push(...m.frames);
        // Для интерполяции нужны только два последних кадра. Раньше очередь
        // чистилась порциями по 200, и после обрезки часы уезжали на полсекунды.
        if (S.man.frames.length > 90) S.man.frames.splice(0, S.man.frames.length - 60);
        for (const f of m.frames) {
          for (const ev of f.events || []) pushLog('man-log', ev);
        }
        if (m.error) toast(m.error, true);
      } else if (m.type === 'end') {
        // Бой закончился сам: показываем итог и сохраняем реплей на сервере.
        for (const f of m.frames || []) S.man.frames.push(f);
        $('man-stop').disabled = true;
        if (m.replay_id) toast(`реплей сохранён: ${m.replay_id}`);
        if (m.error) toast(m.error, true);
        loadReplays();
        keysDown.clear();
      }
    };
    ws.onclose = () => {
      $('man-stop').disabled = true;
      if (S.man) S.man.done = true;
      S.man = null;
    };
  } catch (e) {
    toast(`не началось: ${e.message}`, true);
  }
});

$('man-stop').addEventListener('click', async () => {
  if (!S.man) return;
  try {
    const r = await api(`/api/manual/stop/${S.man.sid}`, { method: 'POST' });
    if (r.result) {
      const pName = $('man-name').value || 'Игрок';
      const fName = S.slots.foe ? S.slots.foe.name : 'Противник';
      const names = S.man.side === 'a' ? [pName, fName] : [fName, pName];
      renderSummary($('man-result'), r.result, names);
    }
    if (r.replay_id) toast(`реплей сохранён: ${r.replay_id}`);
    S.man.ws.close();
  } catch (e) {
    toast(e.message, true);
  }
});

function pushLog(which, ev) {
  const box = $(which);
  if (!box) return;
  const line = el('div', 'row');
  line.appendChild(el('span', 't', (ev.t || 0).toFixed(1)));
  line.appendChild(el('span', KIND_CLASS[ev.kind] || '', describeEvent(ev)));
  box.appendChild(line);
  while (box.children.length > 200) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

/**
 * Раз в 40 мс шлёт ввод в открытый бой.
 *
 * Таймер один: раньше он плодился на каждый старт и отправлял
 * ввод пачками, из-за чего сеть захлёбывалась и управление «залипало».
 */
function sendInput() {
  const m = gameSession();
  if (!m || !m.ws || m.ws.readyState !== 1) return;
  // Если WS не открыт, сбрасываем ввод: иначе накопленные клавиши оживут
  // при переподключении и танк едет сам.
  if (!m.aimReady) m.aim = null;
  const box = $('man-reveal');
  m.ws.send(JSON.stringify({
    keys: [...keysDown],
    aim: m.aim || [0, 0],
    fire: keysDown.has(' ') || mouseDown,
    // Галочку шлём каждый раз: противника можно включать и выключать
    // прямо посреди боя, перезапускать бой не нужно.
    reveal: box ? box.checked : false,
  }));
}

const cvMan = $('cv-man');

/** Наведение мыши записывает прицел в сессию того боя, чьё поле нажали. */
function aimOnMove(sess, view, e) {
  if (!sess || !view) return;
  const r = e.currentTarget.getBoundingClientRect();
  sess.aim = [
    ((e.clientX - r.left) / r.width) * view.w,
    ((e.clientY - r.top) / r.height) * view.h,
  ];
  sess.aimReady = true;
}

cvMan.addEventListener('mousemove', (e) => aimOnMove(S.man, S.manView, e));
cvMan.addEventListener('mousedown', () => { mouseDown = true; cvMan.focus(); });
window.addEventListener('mouseup', () => { mouseDown = false; });
// Игровые клавиши задаём ФИЗИЧЕСКИМИ кодаМИ (KeyboardEvent.code), а не
// символами: при русской раскладке e.key у W — это «ц», список не совпадал,
// газ и поворот корпуса до сервера не доходили вовсе.
const GAME_KEYS = {
  KeyW: 'w', KeyA: 'a', KeyS: 's', KeyD: 'd', Space: ' ',
  ArrowUp: 'w', ArrowLeft: 'a', ArrowDown: 's', ArrowRight: 'd'
};
const GAME_KEYS_FALLBACK = {
  w: 'w', a: 'a', s: 's', d: 'd', ' ': ' ',
  arrowup: 'w', arrowleft: 'a', arrowdown: 's', arrowright: 'd'
};

function gameKey(e) {
  if (!e) return null;
  if (GAME_KEYS[e.code]) return GAME_KEYS[e.code];
  // Подстраховка для окружений без code и для экранных клавиатур.
  return GAME_KEYS_FALLBACK[(e.key || '').toLowerCase()] || null;
}

/**: Поля, в которые клавиши должны идти в текст, а не в танк. */
function isTypingTarget(el) {
  if (!el || !el.tagName) return false;
  if (el.tagName === 'TEXTAREA' || el.isContentEditable) return true;
  if (el.tagName !== 'INPUT') return el.tagName === 'SELECT';
  return !['checkbox', 'radio', 'range', 'button'].includes(el.type);
}

window.addEventListener('keydown', (e) => {
  const k = gameKey(e);
  if (!k) return;
  if (!liveGameOpen()) return;
  // Если фокус в поле ввода — буквы печатаем, но Space всё равно не должен
  // прокручивать страницу и «стрелять» из-под поля.
  if (isTypingTarget(e.target)) return;
  keysDown.add(k);
  e.preventDefault();
  const cv = gameCanvas();
  if (document.activeElement !== cv) cv.focus();
});
window.addEventListener('keyup', (e) => {
  const k = gameKey(e);
  if (k) keysDown.delete(k);
});
// Потеря фокуса окна означает, что keyup мы не увидим: сбрасываем всё,
// иначе газ «залипает» и танк едет сам.
window.addEventListener('blur', () => keysDown.clear());

/**
 * Плавная отрисовка живого боя.
 *
 * Сервер шлёт кадры пачками по 8 штук (60 Гц симуляции против 90 Гц опроса
 * writer'а), а на экране раньше рисовался только последний кадр пачки.
 * Танк прыгал на 8 тиков = 133 мс вперёд каждый раз — отсюда дёрганность.
 * Теперь держим очередь и рисуем по своим часам, подмешивая положение
 * между двумя соседними кадрами сервера.
 */
const manClock = { t: 0, last: 0 };

/** Угол с учётом перехода через 2π: короткая дуга, без прыжка на 359°. */
function lerpAngle(a, b, k) {
  let d = b - a;
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  return a + d * k;
}

/** Смешивает два снимка танка; недостающие поля берём из более нового. */
function lerpTank(a, b, k) {
  if (!a) return b;
  if (!b) return a;
  return {
    x: a.x + (b.x - a.x) * k,
    y: a.y + (b.y - a.y) * k,
    h: lerpAngle(a.h, b.h, k),
    u: lerpAngle(a.u, b.u, k),
    hp: b.hp, cd: b.cd, alive: b.alive, bad: b.bad, vis: b.vis,
  };
}

/** Снаряды сопоставляем по id позиции: интерполируем между теми же двумя. */
function lerpShells(oldSh, newSh, k) {
  if (!oldSh || !oldSh.length || !newSh || !newSh.length) return newSh || oldSh || [];
  return newSh.map((s) => {
    const prev = oldSh.find((o) => o[4] === s[4] && Math.abs(o[0] - s[0]) < 120
      && Math.abs(o[1] - s[1]) < 120);
    if (!prev) return s;
    return [prev[0] + (s[0] - prev[0]) * k, prev[1] + (s[1] - prev[1]) * k, s[2], s[3], s[4]];
  });
}

/** Сцена из потока кадров: два соседних снимка, смешанные по своим часам. */
function frameScene(m, clock) {
  const q = m.frames;
  if (!q.length) return null;
  const last = q[q.length - 1];

  // Сколько секунд прошло с прошлого кадра сервера (нужно для интерполяции).
  const wall = performance.now() / 1000;
  if (!clock.last) clock.last = wall;
  const dt = Math.min(0.1, wall - clock.last);
  clock.last = wall;

  // Играем по времени сервера, а не по порядку прихода: иначе при отставании
  // сети кадры проигрываются быстрее и картинка дёргается.
  // Буфер уменьшен до 1 кадра (минимум) для снижения задержки.
  if (!clock.t || clock.t < last.t - 0.25 || clock.t > last.t + 0.25) {
    clock.t = Math.max(last.t - 1 / 60, clock.t);
  }
  clock.t += dt;

  let a = last;
  let b = last;
  let k = 0;
  if (q.length > 1 && clock.t < last.t) {
    for (let i = q.length - 1; i > 0; i--) {
      if (clock.t >= q[i - 1].t && clock.t <= q[i].t) {
        a = q[i - 1];
        b = q[i];
        const span = b.t - a.t;
        k = span > 1e-6 ? (clock.t - a.t) / span : 1;
        break;
      }
    }
    if (clock.t < q[0].t) {
      a = q[0];
      b = q[0];
      clock.t = q[0].t;
    }
  } else if (clock.t >= last.t) {
    clock.t = last.t;
  }

  return {
    t: clock.t,
    sh: lerpShells(a.bullets, b.bullets, k),
    me: lerpTank(a.me, b.me, k),
    enemy: lerpTank(a.enemy, b.enemy, k),
    enemy_seen: !!(a.enemy_seen || b.enemy_seen),
    side: m.side,
    frame: b,
  };
}

/**
 * Рисует живой бой: свой танк и противник по правилам секретности.
 */
function drawLive(view, s, side, opts) {
  // Пока противника не видно, рисуем его далеко за полем, а не в начале карты.
  const gone = { x: -999, y: -999, h: 0, u: 0, hp: 0, cd: 0, alive: 0, vis: 0 };
  const me = s.me;
  const enemy = s.enemy;
  view.draw({
    t: s.t, sh: s.sh,
    a: side === 'a' ? me : (enemy || gone),
    b: side === 'a' ? (enemy || gone) : me,
  }, {
    // Свой конус зрения показываем по галочке: сервер всё равно не отдаёт
    // невидимого противника, но без конуса непонятно, куда смотреть.
    vision: opts.vision, grid: false, trails: null,
    range: S.balance && S.balance.view_range,
    cone: S.balance && S.balance.view_cone,
    pov: side,
    reveal: !!(s.frame && s.frame.enemy_revealed),
    foe: enemy,
    foeVision: !!(opts.foeVision && enemy),
    fx: opts.fx,
  });
}

// ручной бой рисуем из потока кадров, а не из реплея
(function manualLoop() {
  requestAnimationFrame(manualLoop);
  const view = S.manView;
  const m = S.man;
  if (!view || !m) return;
  const s = frameScene(m, manClock);
  if (!s) {
    if (m.map && m.map.spawns) view.draw({ spawns: m.map.spawns });
    return;
  }
  const box = $('man-vision');
  const foeBox = $('man-foe-vision');
  const fx = fxLive(m, manClock.t, m.side, s);
  drawLive(view, s, m.side, { vision: box ? box.checked : true,
                             foeVision: foeBox && foeBox.checked, fx });
  const f = s.frame;
  if (f.over && f.result && manClock.t >= f.t - 0.001 && !m.ended) {
    if (!m.overAt) m.overAt = performance.now();
    // После уничтожения концовку держим ещё секунду — пусть догорит взрыв.
    const wait = m.deathSeen ? END_LINGER_MS : 0;
    if (performance.now() - m.overAt >= wait) {
      m.ended = true;
      m.summaryRendered = true;
      const pName = $('man-name').value || 'Игрок';
      const fName = S.slots.foe ? S.slots.foe.name : 'Противник';
      const names = m.side === 'a' ? [pName, fName] : [fName, pName];
      renderSummary($('man-result'), f.result, names);
    }
  }

  const isFS = !!document.fullscreenElement;
  const overlay = $('man-repeat');
  if (overlay) overlay.hidden = !(m.ended && isFS);
})();

// --- реплеи -----------------------------------------------------------------

async function loadReplays() {
  const box = $('rep-list');
  box.innerHTML = '';
  let list = [];
  try {
    list = await api('/api/replays');
  } catch (e) {
    box.textContent = `не удалось: ${e.message}`;
    return;
  }
  if (!list.length) { box.textContent = 'пока пусто — рассчитай бой'; return; }
  for (const r of list) {
    const row = el('div', 'row');
    const s = r.summary || {};
    const meta = r.meta || {};
    const names = [meta.a && meta.a.name, meta.b && meta.b.name].filter(Boolean).join(' — ');
    const when = new Date((r.created || 0) * 1000).toLocaleString('ru-RU');
    row.appendChild(el('span', s.outcome === 'draw' ? 'b' : 'a',
      `${names || 'бой'}`));
    row.appendChild(el('span', 't', ` ${when}`));
    row.onclick = async () => {
      try {
        const d = await api(`/api/replay/${r.id}`);
        await showReplay(d, 'rep');
      } catch (e) {
        toast(e.message, true);
      }
    };
    box.appendChild(row);
  }
}

$('rep-refresh').addEventListener('click', loadReplays);

// --- старт ------------------------------------------------------------------

// Ввод шлём по таймеру, а не по каждому нажатию: так клавиши не теряются
// при обрыве соединения и не задваиваются.
setInterval(sendInput, 40);

$('btn-sim-repeat').addEventListener('click', () => {
  $('sim-repeat').hidden = true;
  if (S.replay) {
    S.replay.i = 0;
    S.replay.playing = true;
  }
});
$('btn-rep-repeat').addEventListener('click', () => {
  $('rep-repeat').hidden = true;
  if (S.repReplay) {
    S.repReplay.i = 0;
    S.repReplay.playing = true;
  }
});
$('btn-man-repeat').addEventListener('click', () => {
  $('man-repeat').hidden = true;
  if (!$('man-start').disabled) $('man-start').click();
});

(async function init() {
  try {
    const h = await api('/health');
    $('engine-status').textContent = `движок готов · ручных ${h.manual}`;
    $('engine-status').classList.add('ok');
  } catch (e) {
    $('engine-status').textContent = 'сервер недоступен';
    $('engine-status').classList.add('err');
    return;
  }
  try {
    await loadCatalog();
  } catch (e) {
    toast(`каталог не загрузился: ${e.message}`, true);
  }
  loadReplays();
  setInterval(async () => {
    try {
      const h = await api('/health');
      $('engine-status').textContent = `движок готов · ручных ${h.manual}`;
    } catch (e) { /* сервер перезапускается */ }
  }, 5000);
})();
