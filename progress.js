'use strict';
/*
 * Live progress dashboard for the export run.
 *   node progress.js          → live, refreshes every 5s
 *   node progress.js --once   → single snapshot
 *   SP_DB=pilot.db node progress.js
 */
const path = require('path');
const Database = require('better-sqlite3');

const DB_FILE = process.env.SP_DB || path.join(__dirname, 'sberpodbor.db');
const ONCE = process.argv.includes('--once');
const EVERY_MS = 5000;

const num = (n) => (n === null || n === undefined ? '—' : n.toLocaleString('ru-RU'));
const bar = (frac, w = 28) => {
  const f = Math.max(0, Math.min(1, frac || 0));
  const full = Math.round(f * w);
  return '█'.repeat(full) + '░'.repeat(w - full);
};
const dur = (sec) => {
  if (!isFinite(sec) || sec <= 0) return '—';
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h > 0 ? `${h}ч ${m}м` : `${m}м`;
};

function snapshot(db) {
  const c = (t) => { try { return db.prepare(`SELECT COUNT(*) c FROM ${t}`).get().c; } catch (e) { return 0; } };
  const meta = {};
  try { for (const r of db.prepare('SELECT k,v FROM meta').all()) meta[r.k] = r.v; } catch (e) {}
  return {
    t: Date.now(),
    pages: c('done_profile_pages'), candmeta: c('done_candmeta'),
    details: c('done_profiles'), activity: c('done_candidates'),
    profiles: c('profiles'), candidates: c('candidates'), vacancies: c('vacancies'), users: c('users'),
    resumes: c('resumes'), contacts: c('contacts'), logs: c('logs'), comments: c('comments'),
    meta,
  };
}

// rolling window for rate
const hist = [];
function rate(field, s) {
  hist.push({ t: s.t, v: s[field] });
  while (hist.length > 2 && s.t - hist[0].t > 120000) hist.shift();
  const first = hist.find((h) => h.v !== undefined);
  if (!first || hist.length < 2) return 0;
  const dt = (s.t - first.t) / 1000;
  const dv = s[field] - first.v;
  return dt > 0 ? dv / dt : 0;
}

function phaseOf(s, totals) {
  if (!s.meta.phase_profiles_done) return 'profiles';
  if (!s.meta.phase_candmeta_done && s.candmeta < totals.profiles) return 'candmeta';
  if (s.details < totals.profiles) return 'details';
  if (s.activity < totals.candidates) return 'activity';
  return 'done';
}

function render(s) {
  const PROFILES_TARGET = Number(s.meta.profiles_total || 0) || s.profiles; // written by export.js once page 1 is read
  const PAGES_TOTAL = Math.ceil(PROFILES_TARGET / 50);
  const totals = {
    pages: s.meta.phase_profiles_done ? s.pages : Math.max(PAGES_TOTAL, s.pages),
    profiles: s.meta.phase_profiles_done ? s.profiles : PROFILES_TARGET,
    candidates: s.candidates,
  };
  const ph = phaseOf(s, totals);

  const rows = [
    ['1. Список профилей', s.pages, totals.pages, 'pages', 'стр.'],
    ['2. Статусы заявок',  s.candmeta, totals.profiles, 'candmeta', 'проф.'],
    ['3. Резюме+контакты', s.details, totals.profiles, 'details', 'проф.'],
    ['4. Логи+комментарии', s.activity, totals.candidates, 'activity', 'заявок'],
  ];

  const out = [];
  out.push('\x1b[1m СБЕРПОДБОР — ЭКСПОРТ \x1b[0m   ' + new Date().toLocaleTimeString('ru-RU') + '   БД: ' + path.basename(DB_FILE));
  out.push('');
  for (const [name, done, total, field, unit] of rows) {
    const active = ph === field;
    const frac = total ? done / total : 0;
    const r = active ? rate(field, s) : 0;
    const eta = active && r > 0 ? dur((total - done) / r) : (done >= total && total > 0 ? 'готово' : '');
    const mark = active ? '\x1b[32m▶\x1b[0m' : (done >= total && total > 0 ? '\x1b[32m✓\x1b[0m' : ' ');
    out.push(` ${mark} ${name.padEnd(20)} ${bar(frac)} ${String(Math.round(frac * 100)).padStart(3)}%  ` +
      `${num(done)} / ${num(total)} ${unit}` +
      (active ? `   \x1b[36m${(r * 60).toFixed(0)} ${unit}/мин\x1b[0m   ETA ${eta}` : (eta ? `   ${eta}` : '')));
  }
  out.push('');
  out.push(' Собрано: ' +
    `профили ${num(s.profiles)} · заявки ${num(s.candidates)} · вакансии ${num(s.vacancies)} · users ${num(s.users)}`);
  out.push('          ' +
    `резюме ${num(s.resumes)} · контакты ${num(s.contacts)} · \x1b[1mлоги ${num(s.logs)}\x1b[0m · \x1b[1mкомментарии ${num(s.comments)}\x1b[0m`);
  out.push('');
  if (ph === 'done') out.push(' \x1b[32m\x1b[1mВСЁ ГОТОВО\x1b[0m');
  else out.push(` Текущая фаза: \x1b[1m${ph}\x1b[0m` + (ONCE ? '' : '   (Ctrl+C — выйти, экспорт продолжит работать)'));
  return out.join('\n');
}

function tick() {
  let db;
  try { db = new Database(DB_FILE, { readonly: true, fileMustExist: true }); }
  catch (e) { console.log('Жду БД ' + DB_FILE + ' …'); return; }
  try {
    const s = snapshot(db);
    if (!ONCE) process.stdout.write('\x1b[2J\x1b[H');
    console.log(render(s));
  } finally { db.close(); }
}

tick();
if (!ONCE) setInterval(tick, EVERY_MS);
