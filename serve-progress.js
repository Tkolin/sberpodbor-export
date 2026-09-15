'use strict';
/*
 * Live HTML progress dashboard for the export run.
 *   node serve-progress.js        → http://localhost:8765
 *   PORT=9000 SP_DB=pilot.db node serve-progress.js
 * Reads the SQLite DB read-only; safe to run while the export is working.
 */
const http = require('http');
const path = require('path');
const Database = require('better-sqlite3');

const DB_FILE = process.env.SP_DB || path.join(__dirname, 'sberpodbor.db');
const PORT = Number(process.env.PORT || 8765);
// total profiles is written to meta by export.js once it reads page 1; before that, fall back to what is stored
const PER_PAGE = 50;

const hist = []; // rolling samples for rate calc

function stats() {
  let db;
  try { db = new Database(DB_FILE, { readonly: true, fileMustExist: true }); }
  catch (e) { return { error: 'Нет БД ' + DB_FILE }; }
  try {
    const c = (t) => { try { return db.prepare(`SELECT COUNT(*) c FROM ${t}`).get().c; } catch (_) { return 0; } };
    const meta = {};
    try { for (const r of db.prepare('SELECT k,v FROM meta').all()) meta[r.k] = r.v; } catch (_) {}
    const s = {
      t: Date.now(),
      pages: c('done_profile_pages'), candmeta: c('done_candmeta'),
      details: c('done_profiles'), activity: c('done_candidates'),
      profiles: c('profiles'), candidates: c('candidates'), vacancies: c('vacancies'), users: c('users'),
      resumes: c('resumes'), contacts: c('contacts'), logs: c('logs'), comments: c('comments'),
      profilesDone: !!meta.phase_profiles_done,
      profilesTarget: Number(meta.profiles_total || 0) || c('profiles'),
    };

    hist.push(s);
    while (hist.length > 2 && s.t - hist[0].t > 180000) hist.shift();
    const base = hist[0];
    const dt = (s.t - base.t) / 1000;
    const rate = (f) => (dt > 5 ? Math.max(0, (s[f] - base[f]) / dt) : 0);

    const pagesTotal = s.profilesDone ? s.pages : Math.max(Math.ceil(s.profilesTarget / PER_PAGE), s.pages);
    const profilesTotal = s.profilesDone ? s.profiles : s.profilesTarget;

    const phases = [
      { key: 'pages',    name: 'Список профилей',   done: s.pages,    total: pagesTotal,    unit: 'стр.' },
      { key: 'candmeta', name: 'Статусы заявок',    done: s.candmeta, total: profilesTotal, unit: 'проф.' },
      { key: 'details',  name: 'Резюме + контакты', done: s.details,  total: profilesTotal, unit: 'проф.' },
      { key: 'activity', name: 'Логи + комментарии', done: s.activity, total: s.candidates,  unit: 'заявок' },
    ];
    let current = null;
    for (const p of phases) {
      p.rate = rate(p.key);
      p.pct = p.total ? Math.min(100, (p.done / p.total) * 100) : 0;
      p.complete = p.total > 0 && p.done >= p.total;
      if (!current && !p.complete) { p.active = true; current = p.key; }
      p.etaSec = p.active && p.rate > 0 ? (p.total - p.done) / p.rate : null;
    }
    // overall ETA: remaining work of all unfinished phases at current observed rate
    const act = phases.find((p) => p.active);
    return { ok: true, s, phases, current, overallEtaSec: act ? act.etaSec : 0 };
  } finally { db.close(); }
}

const HTML = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>СберПодбор — экспорт</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#252a34;--tx:#e6e9ef;--dim:#8b93a3;--ok:#3ddc84;--ac:#4da3ff;--warn:#ffb020}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif;padding:24px}
.wrap{max-width:900px;margin:0 auto}
h1{font-size:18px;margin:0 0 2px;letter-spacing:.3px}
.sub{color:var(--dim);font-size:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:14px}
.ph{display:grid;grid-template-columns:170px 1fr 92px;gap:12px;align-items:center;margin:12px 0}
.ph .nm{font-weight:600;font-size:13px;display:flex;align-items:center;gap:7px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--line);flex:none}
.dot.a{background:var(--ac);box-shadow:0 0 0 3px rgba(77,163,255,.18);animation:p 1.4s ease-in-out infinite}
.dot.c{background:var(--ok)}
@keyframes p{50%{opacity:.45}}
.track{height:9px;background:#0b0d11;border-radius:99px;overflow:hidden;border:1px solid var(--line)}
.fill{height:100%;background:linear-gradient(90deg,#2f7ad6,#4da3ff);border-radius:99px;transition:width .6s ease}
.fill.c{background:linear-gradient(90deg,#2a9d5c,#3ddc84)}
.pct{text-align:right;font-variant-numeric:tabular-nums;font-weight:600;font-size:13px}
.meta{grid-column:2/4;display:flex;justify-content:space-between;color:var(--dim);font-size:11.5px;margin-top:-6px;font-variant-numeric:tabular-nums}
.rate{color:var(--ac)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:10px}
.k{background:#0b0d11;border:1px solid var(--line);border-radius:9px;padding:11px 13px}
.k .l{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.k .v{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:3px}
.k.hi .v{color:var(--ok)}
.eta{display:flex;gap:22px;align-items:baseline;flex-wrap:wrap}
.eta b{font-size:22px;font-variant-numeric:tabular-nums}
.err{color:var(--warn)}
</style></head><body><div class="wrap">
<h1>СберПодбор — экспорт базы</h1>
<div class="sub" id="sub">подключение…</div>
<div class="card" id="phases"></div>
<div class="card"><div class="eta" id="eta"></div></div>
<div class="card"><div class="grid" id="counts"></div></div>
</div><script>
const f=n=>(n==null?'—':n.toLocaleString('ru-RU'));
const dur=s=>{if(!s||!isFinite(s)||s<=0)return'—';const h=Math.floor(s/3600),m=Math.round(s%3600/60);return h?h+'ч '+m+'м':m+'м';};
async function tick(){
 let d;try{d=await(await fetch('/api/stats')).json()}catch(e){document.getElementById('sub').innerHTML='<span class=err>сервер недоступен</span>';return}
 if(d.error){document.getElementById('sub').innerHTML='<span class=err>'+d.error+'</span>';return}
 document.getElementById('sub').textContent='обновлено '+new Date().toLocaleTimeString('ru-RU')+' · автообновление 3с';
 document.getElementById('phases').innerHTML=d.phases.map(p=>{
  const cls=p.complete?'c':(p.active?'a':'');
  return '<div class="ph"><div class="nm"><span class="dot '+cls+'"></span>'+p.name+'</div>'+
   '<div class="track"><div class="fill '+(p.complete?'c':'')+'" style="width:'+p.pct.toFixed(1)+'%"></div></div>'+
   '<div class="pct">'+p.pct.toFixed(1)+'%</div>'+
   '<div class="meta"><span>'+f(p.done)+' / '+f(p.total)+' '+p.unit+'</span><span>'+
   (p.active?'<span class=rate>'+(p.rate*60).toFixed(0)+' '+p.unit+'/мин</span> · осталось '+dur(p.etaSec):(p.complete?'готово':''))+
   '</span></div></div>';
 }).join('');
 document.getElementById('eta').innerHTML=
  '<div><div class="l" style="color:var(--dim);font-size:11px">ТЕКУЩАЯ ФАЗА</div><b>'+(d.current||'готово')+'</b></div>'+
  '<div><div class="l" style="color:var(--dim);font-size:11px">ДО КОНЦА ФАЗЫ</div><b>'+dur(d.overallEtaSec)+'</b></div>';
 const s=d.s,K=[['профили',s.profiles],['заявки',s.candidates],['вакансии',s.vacancies],['users',s.users],
  ['резюме',s.resumes],['контакты',s.contacts],['логи',s.logs,1],['комментарии',s.comments,1]];
 document.getElementById('counts').innerHTML=K.map(([l,v,hi])=>'<div class="k'+(hi?' hi':'')+'"><div class="l">'+l+'</div><div class="v">'+f(v)+'</div></div>').join('');
}
tick();setInterval(tick,3000);
</script></body></html>`;

http.createServer((req, res) => {
  if (req.url.startsWith('/api/stats')) {
    res.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
    res.end(JSON.stringify(stats()));
  } else {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    res.end(HTML);
  }
}).listen(PORT, () => console.log(`Дашборд: http://localhost:${PORT}  (БД: ${DB_FILE})`));
