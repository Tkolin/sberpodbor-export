'use strict';
/*
 * СберПодбор exporter → SQLite. Multi-account / multi-proxy, resumable, throttled.
 *
 * Each account = one "lane" with its own token (one active token per user) and its own
 * proxy IP. Work is sharded across lanes; all lanes write to one SQLite DB (single process,
 * so no cross-process DB contention). If one lane hits the anti-abuse 401 block it re-logins
 * via POST /v2/auth/login and backs off; the other lanes keep going.
 *
 * Setup:  npm install   (better-sqlite3 + undici)
 *         cp accounts.example.json accounts.json  &&  edit accounts.json
 * Run:    node export.js
 * Resume: just run again (checkpoints in DB).
 *
 * Measured limits (see API.md): no pagination cap; server self-throttles by latency
 * (~1.3-2.2s/req, ceiling ~4-5 rps per account); the block is triggered by rapid LOGINS,
 * not data volume, and clears on a full /v2/auth/login.
 */

const fs = require('fs');
const path = require('path');
const Database = require('better-sqlite3');
const { fetch, ProxyAgent } = require('undici');

// ─────────────────────────── CONFIG ───────────────────────────
const CONFIG = {
  base:   'https://api.sberpodbor.ru',
  dbFile: process.env.SP_DB || path.join(__dirname, 'sberpodbor.db'),

  perLaneConcurrency: Number(process.env.SP_LANE_CONC || 6), // in-flight per account (~4-5 rps each)
  minIntervalMs:      Number(process.env.SP_INTERVAL_MS || 120),
  minReloginMs:       Number(process.env.SP_RELOGIN_MS || 90 * 1000), // per lane; rapid logins are what trip the block
  profilesPerPage:    50,

  fetchResumes:  true,
  fetchContacts: true,
  fetchLogs:     true,
  fetchComments: true,

  blockCooldownMs:    Number(process.env.SP_BLOCK_MS || 3 * 60 * 1000),
  blockCooldownMaxMs: 20 * 60 * 1000,
  maxRetriesPerReq:   6,
  reqTimeoutMs:       Number(process.env.SP_REQ_TIMEOUT_MS || 30000), // a hung proxy must fail fast, not stall for undici's 5-min default
};

function loadAccounts() {
  const f = path.join(__dirname, 'accounts.json');
  if (fs.existsSync(f)) return JSON.parse(fs.readFileSync(f, 'utf8'));
  if (process.env.SP_EMAIL && process.env.SP_PASSWORD)
    return [{ email: process.env.SP_EMAIL, password: process.env.SP_PASSWORD, proxy: process.env.SP_PROXY || null }];
  throw new Error('No accounts.json and no SP_EMAIL/SP_PASSWORD env. Copy accounts.example.json → accounts.json.');
}
// ───────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString(), ...a);

// ───────────────────────────── DB ─────────────────────────────
const db = new Database(CONFIG.dbFile);
db.pragma('journal_mode = WAL');
db.pragma('busy_timeout = 10000');
db.exec(`
CREATE TABLE IF NOT EXISTS meta        (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS profiles    (id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, middle_name TEXT,
                                        phone TEXT, email TEXT, city TEXT, cur_position TEXT, cur_company TEXT,
                                        experience TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS vacancies   (id INTEGER PRIMARY KEY, title TEXT, status TEXT, city TEXT,
                                        persons_count INTEGER, desired_closing_at TEXT, created_at TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS users       (id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, middle_name TEXT,
                                        email TEXT, role TEXT, position TEXT, status TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS candidates  (candidate_id INTEGER PRIMARY KEY, profile_id INTEGER, vacancy_id INTEGER,
                                        vacancy_title TEXT, status_id INTEGER, status_title TEXT,
                                        created_at TEXT, recruiters TEXT, managers TEXT);
CREATE TABLE IF NOT EXISTS resumes     (profile_id INTEGER PRIMARY KEY, body_html TEXT, source TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS contacts    (id INTEGER PRIMARY KEY, candidate_id INTEGER, profile_id INTEGER,
                                        type TEXT, value TEXT, is_main INTEGER);
CREATE TABLE IF NOT EXISTS logs        (id INTEGER PRIMARY KEY, candidate_id INTEGER, date_time_at TEXT,
                                        message TEXT, user_full_name TEXT);
CREATE TABLE IF NOT EXISTS comments    (id INTEGER PRIMARY KEY, candidate_id INTEGER, comment TEXT,
                                        created_at TEXT, changed_at TEXT, user_id INTEGER, user_full_name TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS done_profiles   (profile_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS done_candidates (candidate_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS done_candmeta   (profile_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS done_profile_pages (page INTEGER PRIMARY KEY);
CREATE INDEX IF NOT EXISTS idx_candidates_profile ON candidates(profile_id);
`);

const metaGet = (k) => { const r = db.prepare('SELECT v FROM meta WHERE k=?').get(k); return r ? r.v : null; };
const metaSet = db.prepare('INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v');

const ins = {
  profile: db.prepare(`INSERT INTO profiles(id,first_name,last_name,middle_name,phone,email,city,cur_position,cur_company,experience,raw)
                       VALUES(@id,@first_name,@last_name,@middle_name,@phone,@email,@city,@cur_position,@cur_company,@experience,@raw)
                       ON CONFLICT(id) DO UPDATE SET first_name=excluded.first_name,last_name=excluded.last_name,phone=excluded.phone,email=excluded.email,city=excluded.city,cur_position=excluded.cur_position,cur_company=excluded.cur_company,experience=excluded.experience,raw=excluded.raw`),
  vacancy: db.prepare(`INSERT INTO vacancies(id,title,status,city,persons_count,desired_closing_at,created_at,raw)
                       VALUES(@id,@title,@status,@city,@persons_count,@desired_closing_at,@created_at,@raw)
                       ON CONFLICT(id) DO UPDATE SET title=excluded.title,status=excluded.status,raw=excluded.raw`),
  user: db.prepare(`INSERT INTO users(id,first_name,last_name,middle_name,email,role,position,status,raw)
                    VALUES(@id,@first_name,@last_name,@middle_name,@email,@role,@position,@status,@raw)
                    ON CONFLICT(id) DO UPDATE SET first_name=excluded.first_name,last_name=excluded.last_name,email=excluded.email,role=excluded.role,raw=excluded.raw`),
  candidate: db.prepare(`INSERT INTO candidates(candidate_id,profile_id,vacancy_id,vacancy_title,status_id,status_title,created_at,recruiters,managers)
                         VALUES(@candidate_id,@profile_id,@vacancy_id,@vacancy_title,@status_id,@status_title,@created_at,@recruiters,@managers)
                         ON CONFLICT(candidate_id) DO UPDATE SET status_id=excluded.status_id,status_title=excluded.status_title,recruiters=excluded.recruiters,managers=excluded.managers`),
  resume: db.prepare(`INSERT OR REPLACE INTO resumes(profile_id,body_html,source,raw) VALUES(?,?,?,?)`),
  contact: db.prepare(`INSERT OR REPLACE INTO contacts(id,candidate_id,profile_id,type,value,is_main) VALUES(?,?,?,?,?,?)`),
  logRow: db.prepare(`INSERT OR REPLACE INTO logs(id,candidate_id,date_time_at,message,user_full_name) VALUES(?,?,?,?,?)`),
  comment: db.prepare(`INSERT OR REPLACE INTO comments(id,candidate_id,comment,created_at,changed_at,user_id,user_full_name,raw) VALUES(?,?,?,?,?,?,?,?)`),
  doneProfile: db.prepare(`INSERT OR IGNORE INTO done_profiles(profile_id) VALUES(?)`),
  doneCandidate: db.prepare(`INSERT OR IGNORE INTO done_candidates(candidate_id) VALUES(?)`),
  doneCandmeta: db.prepare(`INSERT OR IGNORE INTO done_candmeta(profile_id) VALUES(?)`),
};

// ───────────────────────── lanes (accounts) ─────────────────────────
const lanes = loadAccounts().map((acc, i) => ({
  i, email: acc.email, password: acc.password,
  dispatcher: acc.proxy ? new ProxyAgent(acc.proxy) : undefined,
  proxyLabel: acc.proxy ? acc.proxy.replace(/\/\/[^@]*@/, '//***@') : 'direct',
  token: null, tokenExpiryMs: 0, lastLoginMs: 0, loginInFlight: null,
  blockMs: CONFIG.blockCooldownMs, lastStart: 0,
}));

async function laneLogin(lane) {
  const r = await fetch(`${CONFIG.base}/v2/auth/login`, {
    method: 'POST', dispatcher: lane.dispatcher, signal: AbortSignal.timeout(CONFIG.reqTimeoutMs),
    headers: { accept: 'application/json, text/plain, */*', 'content-type': 'application/vnd.api+json' },
    body: JSON.stringify({ data: { attributes: { login: lane.email, password: lane.password } } }),
  });
  if (!r.ok) throw new Error(`login ${lane.email} failed: ${r.status}`);
  const a = (await r.json()).data.attributes;
  lane.token = a.accessToken;
  lane.tokenExpiryMs = Date.parse(a.accessTokenExpiryAt) || (Date.now() + 7 * 3600e3);
  lane.lastLoginMs = Date.now();
  log(`[lane ${lane.i} ${lane.email} via ${lane.proxyLabel}] logged in until`, a.accessTokenExpiryAt);
  return lane.token;
}

async function ensureLaneToken(lane) {
  if (lane.token && Date.now() < lane.tokenExpiryMs - 5 * 60e3) return lane.token;
  if (!lane.loginInFlight) {
    const wait = Math.max(0, lane.lastLoginMs + CONFIG.minReloginMs - Date.now());
    lane.loginInFlight = (wait ? sleep(wait) : Promise.resolve())
      .then(() => laneLogin(lane))
      .finally(() => { lane.loginInFlight = null; });
  }
  return lane.loginInFlight;
}

// per-lane pacing: min gap between request starts
async function lanePace(lane) {
  const now = Date.now();
  const wait = Math.max(0, lane.lastStart + CONFIG.minIntervalMs - now);
  lane.lastStart = now + wait;
  if (wait) await sleep(wait);
}

async function laneApi(lane, pathUrl, { method = 'GET', body = null, jsonApi = false } = {}) {
  for (let attempt = 0; attempt < CONFIG.maxRetriesPerReq; attempt++) {
    let r, text, usedToken;
    try {
      // ensureLaneToken does a raw fetch (login) that can throw on a proxy blip; it must be
      // inside the try or the throw escapes laneApi and the whole work item is silently dropped.
      await ensureLaneToken(lane);
      await lanePace(lane);
      usedToken = lane.token;
      const headers = { accept: 'application/json, text/plain, */*', authorization: 'Bearer ' + lane.token };
      if (body != null) headers['content-type'] = jsonApi ? 'application/vnd.api+json' : 'application/json';
      r = await fetch(CONFIG.base + pathUrl, { method, dispatcher: lane.dispatcher, headers, signal: AbortSignal.timeout(CONFIG.reqTimeoutMs), body: body != null ? JSON.stringify(body) : undefined });
      text = await r.text();
    } catch (e) {
      log(`[lane ${lane.i}] net error`, pathUrl, String(e)); await sleep(1500 * (attempt + 1)); continue;
    }
    if (r.status === 200) { lane.blockMs = CONFIG.blockCooldownMs; return text ? JSON.parse(text) : null; }
    if (r.status === 404) return { __notfound: true };
    if (r.status === 401) {
      // Only the worker that actually used the now-dead token invalidates it. Otherwise every
      // in-flight worker on this lane nulls the token its neighbour just refreshed, and the lane
      // logs in once per worker — a login storm, which is exactly what trips the anti-abuse
      // block (API.md: the block is about LOGIN rate, not data volume).
      const stale = lane.token === usedToken;
      if (stale) {
        lane.token = null;
        log(`[lane ${lane.i}] 401 on ${pathUrl} — re-login + back-off ${Math.round(lane.blockMs / 1000)}s`);
        lane.blockMs = Math.min(lane.blockMs * 2, CONFIG.blockCooldownMaxMs);
      }
      try { await ensureLaneToken(lane); } catch (e) { log(`[lane ${lane.i}] re-login err`, String(e)); }
      await sleep(stale ? lane.blockMs : 2000);
      continue;
    }
    if (r.status === 429 || r.status >= 500) { await sleep(1500 * (attempt + 1)); continue; }
    // 403 is the server's final answer for records it won't release (proven stable across runs and
    // proxies). Treat it as "no data" rather than a failure, or the profile is deferred forever.
    if (r.status === 403) { log(`[lane ${lane.i}] 403 forbidden (no-data)`, pathUrl); return { __forbidden: true }; }
    log(`[lane ${lane.i}] unexpected ${r.status} ${pathUrl}`, (text || '').slice(0, 120));
    return null;
  }
  log(`[lane ${lane.i}] GAVE UP`, pathUrl);
  return null;
}

// Shard an array of work items across all lanes, perLaneConcurrency runners each.
async function shard(items, worker) {
  let idx = 0;
  const next = () => (idx < items.length ? items[idx++] : undefined);
  const runners = [];
  for (const lane of lanes) {
    for (let c = 0; c < CONFIG.perLaneConcurrency; c++) {
      runners.push((async () => {
        for (let it = next(); it !== undefined; it = next()) {
          try { await worker(lane, it); } catch (e) { log(`[lane ${lane.i}] worker err`, String(e)); }
        }
      })());
    }
  }
  await Promise.all(runners);
}

// Sequential paged fetch on lane 0 (cheap phases: vacancies, users, profiles list).
async function pagedPhase(pathBuilder, handler, { doneKey, pageKey, per = 50 }) {
  if (metaGet(doneKey)) return log(doneKey, 'already done');
  const lane = lanes[0];
  let page = Number(metaGet(pageKey) || 1), total = Infinity;
  while ((page - 1) * per < total) {
    const built = pathBuilder(page, per);
    const j = await laneApi(lane, built.url, built.opts || {});
    if (!j || !j.data) { log(doneKey, 'stopped at page', page); break; }
    total = j.meta ? j.meta.totalItems : total;
    handler(j.data);
    if (page % 25 === 0 || page === 1) log(doneKey, `page ${page}/${Math.ceil(total / per)} total=${total}`);
    if (j.data.length === 0) break;
    page++; metaSet.run(pageKey, String(page));
  }
  metaSet.run(doneKey, '1');
}

// ───────────────────────── phases ─────────────────────────
async function phaseVacancies() {
  await pagedPhase(
    (page, per) => ({ url: `/v2/vacancies?page=${page}&itemsPerPage=${per}` }),
    (rows) => { const tx = db.transaction((rs) => rs.forEach((v) => { const a = v.attributes;
      ins.vacancy.run({ id: a._id, title: a.title, status: a.status, city: a.city, persons_count: a.personsCount,
        desired_closing_at: a.desiredClosingAt, created_at: a.createdAt, raw: JSON.stringify(v) }); })); tx(rows); },
    { doneKey: 'phase_vacancies_done', pageKey: 'vac_page' });
}

async function phaseUsers() {
  await pagedPhase(
    (page, per) => ({ url: `/v2/users?page=${page}&itemsPerPage=${per}` }),
    (rows) => { const tx = db.transaction((rs) => rs.forEach((u) => { const a = u.attributes;
      ins.user.run({ id: Number(a._id), first_name: a.firstName, last_name: a.lastName, middle_name: a.middleName,
        email: a.email, role: a.role, position: a.position, status: a.status, raw: JSON.stringify(u) }); })); tx(rows); },
    { doneKey: 'phase_users_done', pageKey: 'usr_page' });
}

function storeProfilePage(rows) {
  const tx = db.transaction((rs) => rs.forEach((p) => { const a = p.attributes; const cw = a.currentWork || {};
    ins.profile.run({ id: a._id, first_name: a.firstName, last_name: a.lastName, middle_name: a.middleName, phone: a.phone,
      email: a.email, city: a.city, cur_position: cw.position, cur_company: cw.company, experience: cw.experience, raw: JSON.stringify(p) });
    for (const v of (a.vacancies || []))
      ins.candidate.run({ candidate_id: v.candidateId, profile_id: a._id, vacancy_id: v.id, vacancy_title: v.title,
        status_id: null, status_title: null, created_at: null, recruiters: null, managers: null });
  })); tx(rows);
}
const listOpts = (page, per) => ({ method: 'POST', jsonApi: true, body: { data: { attributes: { page, itemsPerPage: per } } } });

// Profiles list: 5327 pages, sharded across all lanes (pages are independent & deterministic).
async function phaseProfiles() {
  if (metaGet('phase_profiles_done')) return log('profiles already done');
  const per = CONFIG.profilesPerPage;
  const first = await laneApi(lanes[0], '/v2/applicant_profiles/list', listOpts(1, per));
  if (!first || !first.data) throw new Error('profiles: cannot read page 1');
  const total = first.meta.totalItems;
  metaSet.run('profiles_total', String(total)); // dashboards (progress.js / serve-progress.js) read this for ETA
  let lastPage = Math.ceil(total / per);
  const cap = Number(process.env.SP_MAX_PROFILE_PAGES || 0); // pilot: limit pages (0 = all)
  if (cap > 0) lastPage = Math.min(lastPage, cap);
  if (!db.prepare('SELECT 1 FROM done_profile_pages WHERE page=1').get()) { storeProfilePage(first.data); db.prepare('INSERT OR IGNORE INTO done_profile_pages(page) VALUES(1)').run(); }
  log('profiles: total=', total, 'pages=', lastPage);
  const doneP = new Set(db.prepare('SELECT page FROM done_profile_pages').all().map(r => r.page));
  const pages = [];
  for (let p = 2; p <= lastPage; p++) if (!doneP.has(p)) pages.push(p);
  let n = doneP.size;
  await shard(pages, async (lane, page) => {
    const j = await laneApi(lane, '/v2/applicant_profiles/list', listOpts(page, per));
    if (j && j.data) { storeProfilePage(j.data); db.prepare('INSERT OR IGNORE INTO done_profile_pages(page) VALUES(?)').run(page); }
    if (++n % 100 === 0) log('  profiles page', n, '/', lastPage);
  });
  if (cap > 0) log('profiles: PILOT cap active (', cap, 'pages) — phase NOT marked done');
  else metaSet.run('phase_profiles_done', '1');
}

// Enrich candidates with status/recruiters (per-profile) — sharded across lanes.
async function phaseCandidateMeta() {
  if (metaGet('phase_candmeta_done')) return log('candmeta already done');
  const todo = db.prepare(`SELECT id FROM profiles WHERE id NOT IN (SELECT profile_id FROM done_candmeta)`).all().map(r => r.id);
  log('candidate meta: profiles to enrich =', todo.length);
  let n = 0;
  await shard(todo, async (lane, pid) => {
    const j = await laneApi(lane, `/v2/applicant_profiles/${pid}/candidates_list?page=1&itemsPerPage=50`);
    if (j && j.data) { const tx = db.transaction((rs) => rs.forEach((c) => { const a = c.attributes;
      ins.candidate.run({ candidate_id: a.candidateId, profile_id: pid, vacancy_id: a.vacancyId, vacancy_title: a.vacancyTitle,
        status_id: a.candidateStatusId, status_title: a.candidateStatusTitle, created_at: a.candidateCreatedAt,
        recruiters: JSON.stringify(a.recruiters || []), managers: JSON.stringify(a.managers || []) }); })); tx(j.data); }
    ins.doneCandmeta.run(pid);
    if (++n % 1000 === 0) log('  candmeta', n, '/', todo.length);
  });
  metaSet.run('phase_candmeta_done', '1');
}

// Per-profile: resume + contacts — sharded.
async function phaseProfileDetails() {
  if (!CONFIG.fetchResumes && !CONFIG.fetchContacts) return;
  const todo = db.prepare(`SELECT id FROM profiles WHERE id NOT IN (SELECT profile_id FROM done_profiles)`).all().map(r => r.id);
  log('profile details (resume+contacts): to do =', todo.length);
  let n = 0;
  let failed = 0;
  await shard(todo, async (lane, pid) => {
    // laneApi returns null only when it truly gave up; {__notfound:true} means "no resume", which
    // is a complete answer. Never checkpoint a profile whose fetch hard-failed — leave it for the
    // next run instead of marking it done with an empty resume.
    let hardFail = false;
    if (CONFIG.fetchResumes) {
      const j = await laneApi(lane, `/v2/applicant_profiles/${pid}/resumes/last?include=source`);
      if (j === null) hardFail = true;
      else if (j.data) { const src = (j.included && j.included[0] && j.included[0].attributes) ? j.included[0].attributes.alias : null;
        ins.resume.run(pid, j.data.attributes ? j.data.attributes.body : null, src, JSON.stringify(j)); }
    }
    if (CONFIG.fetchContacts) {
      const cidRow = db.prepare(`SELECT candidate_id FROM candidates WHERE profile_id=? LIMIT 1`).get(pid);
      if (cidRow) {
        const c = await laneApi(lane, `/v2/candidates/${cidRow.candidate_id}`);
        if (c === null) hardFail = true;
        const refs = c && c.data && c.data.relationships && c.data.relationships.profileContacts;
        for (const ref of (refs && refs.data ? refs.data : [])) {
          const contactId = Number(String(ref.id).split('/').pop());
          const pc = await laneApi(lane, `/v2/profile_contacts/${contactId}`);
          if (pc === null) hardFail = true;
          else if (pc.data) { const a = pc.data.attributes;
            ins.contact.run(a._id, cidRow.candidate_id, pid, a.type, a.value, a.isMain ? 1 : 0); }
        }
      }
    }
    if (hardFail) { failed++; return; }
    ins.doneProfile.run(pid);
    if (++n % 1000 === 0) log('  details', n, '/', todo.length, failed ? `(deferred ${failed})` : '');
  });
  log('profile details finished:', n, 'done,', failed, 'deferred to next run');
}

// Per-candidate: logs (п.3) + comments (п.4) — sharded. The heavy phase.
async function phaseCandidateActivity() {
  if (!CONFIG.fetchLogs && !CONFIG.fetchComments) return;
  const todo = db.prepare(`SELECT candidate_id FROM candidates WHERE candidate_id NOT IN (SELECT candidate_id FROM done_candidates)`).all().map(r => r.candidate_id);
  log('candidate activity (logs+comments): to do =', todo.length);
  let n = 0;
  let failed = 0;
  await shard(todo, async (lane, cid) => {
    let hardFail = false;
    if (CONFIG.fetchLogs) {
      const j = await laneApi(lane, `/v2/candidates/${cid}/logs`);
      if (j === null) hardFail = true;
      if (j && j.data) { const tx = db.transaction((rs) => rs.forEach((l) => { const a = l.attributes;
        ins.logRow.run(a.id, cid, a.dateTimeAt, a.message, a.userFullName); })); tx(j.data); }
    }
    if (CONFIG.fetchComments) {
      const j = await laneApi(lane, `/v2/candidates/${cid}/comments?include=user`);
      if (j === null) hardFail = true;
      if (j && j.data) {
        const uById = {};
        for (const inc of (j.included || [])) if (inc.type === 'User') { const a = inc.attributes; uById[inc.id] = [a.firstName, a.lastName].filter(Boolean).join(' '); }
        const tx = db.transaction((rs) => rs.forEach((c) => { const a = c.attributes;
          const uref = c.relationships && c.relationships.user && c.relationships.user.data;
          const uid = uref ? Number(String(uref.id).split('/').pop()) : null;
          ins.comment.run(a._id, cid, a.comment, a.createdAt, a.changedAt, uid, uref ? uById[uref.id] : null, JSON.stringify(c)); })); tx(j.data);
      }
    }
    // Same rule as the details phase: a hard failure must not be checkpointed as "done with
    // no logs" — leave it queued for the next run.
    if (hardFail) { failed++; return; }
    ins.doneCandidate.run(cid);
    if (++n % 1000 === 0) log('  activity', n, '/', todo.length, failed ? `(deferred ${failed})` : '');
  });
  log('candidate activity finished:', n, 'done,', failed, 'deferred to next run');
}

// ───────────────────────── main ─────────────────────────
(async () => {
  log('DB:', CONFIG.dbFile, '| lanes:', lanes.map(l => `${l.email}@${l.proxyLabel}`).join(', '), '| perLaneConc:', CONFIG.perLaneConcurrency);
  await Promise.all(lanes.map(ensureLaneToken)); // warm up all logins once (sequential enough)
  await phaseUsers();
  await phaseVacancies();
  await phaseProfiles();
  await phaseCandidateMeta();
  await phaseProfileDetails();
  await phaseCandidateActivity();
  log('DONE.',
    'profiles=', db.prepare('SELECT COUNT(*) c FROM profiles').get().c,
    'vacancies=', db.prepare('SELECT COUNT(*) c FROM vacancies').get().c,
    'candidates=', db.prepare('SELECT COUNT(*) c FROM candidates').get().c,
    'resumes=', db.prepare('SELECT COUNT(*) c FROM resumes').get().c,
    'contacts=', db.prepare('SELECT COUNT(*) c FROM contacts').get().c,
    'logs=', db.prepare('SELECT COUNT(*) c FROM logs').get().c,
    'comments=', db.prepare('SELECT COUNT(*) c FROM comments').get().c);
  db.close();
})().catch((e) => { log('FATAL', e); process.exit(1); });
