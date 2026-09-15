'use strict';
// Validate each (account + proxy) lane end-to-end: proxy egress IP, /v2 login, and a data call.
const fs = require('fs');
const path = require('path');
const { fetch, ProxyAgent } = require('undici');
const BASE = 'https://api.sberpodbor.ru';
const accounts = JSON.parse(fs.readFileSync(path.join(__dirname, 'accounts.json'), 'utf8'));

(async () => {
  for (const acc of accounts) {
    const label = `${acc.email} @ ${acc.proxy ? acc.proxy.replace(/\/\/[^@]*@/, '//***@') : 'direct'}`;
    const dispatcher = acc.proxy ? new ProxyAgent(acc.proxy) : undefined;
    const out = { lane: label };
    try {
      // egress IP through the proxy
      try {
        const ipr = await fetch('https://api.ipify.org?format=json', { dispatcher });
        out.egressIp = (await ipr.json()).ip;
      } catch (e) { out.egressIp = 'ip-check-failed: ' + String(e).slice(0, 60); }

      // /v2 login
      let tok = null;
      const v2 = await fetch(`${BASE}/v2/auth/login`, {
        method: 'POST', dispatcher,
        headers: { accept: 'application/json, text/plain, */*', 'content-type': 'application/vnd.api+json' },
        body: JSON.stringify({ data: { attributes: { login: acc.email, password: acc.password } } }),
      });
      out.v2_login = v2.status;
      if (v2.ok) tok = (await v2.json()).data.attributes.accessToken;
      else {
        // fallback: /v1 login
        const v1 = await fetch(`${BASE}/v1/auth/login.json`, {
          method: 'POST', dispatcher,
          headers: { accept: 'application/json', 'content-type': 'application/json' },
          body: JSON.stringify({ request: { auth: { login: acc.email, password: acc.password } } }),
        });
        out.v1_login = v1.status;
        if (v1.ok) tok = (await v1.json()).response.data.attributes.access_token;
      }

      // data call
      if (tok) {
        const r = await fetch(`${BASE}/v2/applicant_profiles/list`, {
          method: 'POST', dispatcher,
          headers: { accept: 'application/json', 'content-type': 'application/vnd.api+json', authorization: 'Bearer ' + tok },
          body: JSON.stringify({ data: { attributes: { page: 1, itemsPerPage: 1 } } }),
        });
        out.data_status = r.status;
        try { out.total = (await r.json()).meta.totalItems; } catch (_) {}
      }
    } catch (e) { out.error = String(e); }
    console.log(JSON.stringify(out));
  }
})();
