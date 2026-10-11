// Runs plugins/silo-webdav/service.js outside Silo: a fake ts.* (torrfs over a
// fixed tree, a login endpoint) and an HTTP server that dispatches like Silo's
// plugin router (/plugins/<id>/*) and streams files with Range like
// http.ServeContent. Used by tests/test_webdav.py.
//
//   node tests/webdav_harness.js <port> [owner password]
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

const PORT = parseInt(process.argv[2] || '18095', 10);
const PASSWORD = process.argv[3] || '';
const ID = 'silo-webdav';
const MTIME = 1760180000;

// Library: two categories, a torrent with a folder, Cyrillic and spaces.
const FILES = {
    'Фильмы/Movie (2026)/movie.mkv': Buffer.from('0123456789'.repeat(1000)),
    'Фильмы/Movie (2026)/Subs/en & ru.srt': Buffer.from('1\n00:00:01,000 --> 00:00:02,000\nhi\n'),
    'Сериалы/Show S01/e01.mp4': Buffer.alloc(4096, 7),
};
function mime(p) {
    return p.endsWith('.mkv') ? 'video/x-matroska' : p.endsWith('.mp4') ? 'video/mp4' : 'application/x-subrip';
}
function node(p) {
    if (FILES[p]) return { name: path.posix.basename(p), path: p, is_dir: false, size: FILES[p].length, mtime: MTIME, mime: mime(p) };
    const isDir = Object.keys(FILES).some(f => f.startsWith(p + '/'));
    return isDir ? { name: path.posix.basename(p), path: p, is_dir: true, size: 0, mtime: 0, mime: '' } : null;
}
function clean(p) { return path.posix.normalize('/' + String(p || '')).replace(/^\/+/, '').replace(/\/+$/, ''); }

const routes = [];
const logs = [];
const ts = {
    info: { plugin_id: ID, version: 'test' },
    web: { route: (m, r, h) => routes.push({ m, r, h }) },
    torrfs: {
        stat: (uid, p) => { if (uid !== 'owner') throw new Error('user'); return node(clean(p)); },
        list: (uid, p) => {
            const base = clean(p);
            const names = new Set();
            for (const f of Object.keys(FILES)) {
                if (base === '' || f.startsWith(base + '/')) {
                    const rest = base === '' ? f : f.substring(base.length + 1);
                    names.add(rest.split('/')[0]);
                }
            }
            return [...names].sort().map(n => node(base ? base + '/' + n : n));
        },
        open: (uid, p) => FILES[clean(p)] ? { file: clean(p) } : null,
    },
    crypto: {
        sha256: s => crypto.createHash('sha256').update(String(s)).digest('hex'),
        base64decode: s => Buffer.from(String(s), 'base64').toString('utf8'),
    },
    http: {
        post: (url, body) => {
            // Silo's /api/auth/login: owner + password.
            const ok = url === 'http://127.0.0.1:' + PORT + '/api/auth/login' &&
                body.username === 'owner' && body.password === PASSWORD;
            logs.push('login ' + body.username + ' ' + (ok ? 'ok' : 'fail'));
            return { ok, status: ok ? 200 : 401,
                     json: () => (ok ? { user: { id: 'owner', username: 'owner', is_banned: false }, token: 't' } : { error: 'x' }) };
        },
    },
};
const sandbox = { ts, console: { log: m => logs.push(String(m)) } };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'plugins', ID, 'service.js'), 'utf8'), sandbox);

function serve(req, res, body) {
    const url = new URL(req.url, 'http://x');
    const reqPath = decodeURIComponent(url.pathname);
    if (url.pathname === '/__log') { res.end(logs.join('\n')); return; }
    if (!reqPath.startsWith('/plugins/' + ID)) { res.writeHead(404); res.end(); return; }
    const route = routes.find(r => r.m === req.method);
    if (!route) { res.writeHead(404, { 'Content-Type': 'application/json' }); res.end('{"error":"route not found in plugin"}'); return; }

    const headers = {};
    for (const [k, v] of Object.entries(req.headers)) headers[k.toLowerCase()] = String(v);
    const jsReq = { method: req.method, path: reqPath, query: {}, headers, raw: body, body,
                    user: PASSWORD ? null : { id: 'owner', username: 'owner', rank: 100 } };
    const out = { status: 200, headers: {}, body: null, ct: null, stream: null, empty: false };
    const jsRes = {
        status(c) { out.status = c; return jsRes; },
        header(k, v) { out.headers[k] = v; return jsRes; },
        text(t) { out.ct = 'text/plain; charset=utf-8'; out.body = String(t); return jsRes; },
        html(t) { out.ct = 'text/html; charset=utf-8'; out.body = String(t); return jsRes; },
        json(o) { out.ct = 'application/json'; out.body = JSON.stringify(o); return jsRes; },
        stream(h, n) { out.stream = h; return jsRes; },
        end() { out.empty = true; return jsRes; },
    };
    route.h(jsReq, jsRes);

    if (out.stream) {                       // like http.ServeContent
        const data = FILES[out.stream.file];
        const h = Object.assign({}, out.headers, { 'Last-Modified': new Date(MTIME * 1000).toUTCString() });
        const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range || '');
        let status = 200, start = 0, end = data.length - 1;
        if (m) {
            if (m[1] === '') { start = data.length - parseInt(m[2], 10); }
            else { start = parseInt(m[1], 10); if (m[2] !== '') end = Math.min(parseInt(m[2], 10), end); }
            status = 206; h['Content-Range'] = 'bytes ' + start + '-' + end + '/' + data.length;
        }
        h['Content-Length'] = end - start + 1;
        res.writeHead(status, h);
        res.end(req.method === 'HEAD' ? undefined : data.subarray(start, end + 1));
        return;
    }
    const h = Object.assign({}, out.headers);
    if (!h['Content-Type'] && out.ct) h['Content-Type'] = out.ct;   // gin keeps a header set first
    res.writeHead(out.status, h);
    res.end(out.empty || out.body === null ? undefined : out.body);
}

http.createServer((req, res) => {
    const chunks = [];
    req.on('data', c => chunks.push(c));
    req.on('end', () => {
        try { serve(req, res, Buffer.concat(chunks).toString('utf8')); }
        catch (e) { res.writeHead(500); res.end(String(e && e.stack || e)); }
    });
}).listen(PORT, '127.0.0.1', () => console.log('listening ' + PORT));
