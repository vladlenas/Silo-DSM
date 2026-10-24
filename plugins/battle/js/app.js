'use strict';

// TorrServer vs Silo. Everything runs here in the browser: both streams are
// read the way a player reads them, and both servers are asked for their own
// torrent statistics once a second. TorrServer allows requests from other
// addresses (CORS), Silo is this page's own server.

var MB = 1024 * 1024;
var START_MB = 32;              // "player can start": first 32 MB delivered
var STALL_MS = 60000;           // no data for this long: the run failed
var RUN_LIMIT_MS = 600000;      // one read at most 10 minutes
var SILO_SLEEP_WAIT_MS = 150000;
var COLORS = { ts: '#e8a33d', silo: '#3aa3a0' };
var NAMES = { ts: 'TorrServer', silo: 'Silo' };

var cfg = {};
var siloFiles = [];
var siloTorrent = null;
var tsFile = null;              // matching file in TorrServer: {id, path, length}
var running = false;
var stopRequested = false;
var currentAbort = null;
var runs = [];                  // finished runs

function t(key, def) {
    if (typeof i18n !== 'undefined' && i18n.t) {
        var v = i18n.t('plugin.battle.' + key, def);
        if (v && v !== 'plugin.battle.' + key) return v;
    }
    return def;
}

function fmt(s) {
    var args = arguments;
    return String(s).replace(/\{(\d)\}/g, function (m, i) { return args[+i + 1]; });
}

function $(id) { return document.getElementById(id); }

function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

function log(msg, cls) {
    var el = $('log');
    var line = document.createElement('div');
    var d = new Date();
    line.textContent = ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2) + ':' +
        ('0' + d.getSeconds()).slice(-2) + '  ' + msg;
    if (cls) line.className = cls;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
}

function setProgress(p) { $('progress').style.width = Math.max(0, Math.min(100, p)) + '%'; }

function setMsg(text, ok) {
    var el = $('check-msg');
    el.textContent = text || '';
    el.className = 'hint ' + (ok === true ? 'msg-ok' : ok === false ? 'msg-err' : '');
}

// ---- settings (this browser only) ----

function loadCfg() {
    try { cfg = JSON.parse(localStorage.getItem('silo-battle') || '{}') || {}; } catch (e) { cfg = {}; }
    if (!cfg.tsUrl) cfg.tsUrl = location.protocol + '//' + location.hostname + ':8090';
    $('f-ts-url').value = cfg.tsUrl;
    $('f-ts-user').value = cfg.tsUser || '';
    $('f-ts-pass').value = cfg.tsPass || '';
    if (cfg.volume) $('f-volume').value = cfg.volume;
    if (cfg.seek != null) $('f-seek').value = cfg.seek;
    if (cfg.rounds) $('f-rounds').value = cfg.rounds;
}

function readCfg() {
    cfg.tsUrl = $('f-ts-url').value.trim().replace(/\/+$/, '');
    if (cfg.tsUrl && !/^https?:\/\//.test(cfg.tsUrl)) cfg.tsUrl = 'http://' + cfg.tsUrl;
    cfg.tsUser = $('f-ts-user').value.trim();
    cfg.tsPass = $('f-ts-pass').value;
    cfg.volume = Math.max(16, parseInt($('f-volume').value, 10) || 300);
    cfg.seek = Math.max(0, parseInt($('f-seek').value, 10) || 0);
    cfg.rounds = Math.max(1, parseInt($('f-rounds').value, 10) || 2);
    cfg.hash = $('f-torrent').value;
    cfg.fileIdx = parseInt($('f-file').value, 10);
    try { localStorage.setItem('silo-battle', JSON.stringify(cfg)); } catch (e) { /* private mode */ }
}

// ---- servers ----

function tsHeaders(extra) {
    var h = extra || {};
    if (cfg.tsUser) h.Authorization = 'Basic ' + btoa(unescape(encodeURIComponent(cfg.tsUser + ':' + cfg.tsPass)));
    return h;
}

function tsTorrents(body) {
    return fetch(cfg.tsUrl + '/torrents', {
        method: 'POST', headers: tsHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(body)
    }).then(function (r) {
        if (r.status === 401) throw new Error(t('err_ts_auth', 'TorrServer asks for a login: enter the login and password.'));
        if (!r.ok) throw new Error('TorrServer HTTP ' + r.status);
        return r.text().then(function (s) { return s ? JSON.parse(s) : null; });
    });
}

function tsStatus(hash) {
    // "list" does not wake the torrent up ("get" does)
    return tsTorrents({ action: 'list' }).then(function (list) {
        list = list || [];
        for (var i = 0; i < list.length; i++) if (list[i].hash === hash) return list[i];
        return null;
    });
}

function siloStatus(hash) {
    return fetch('/api/torrents/' + hash).then(function (r) {
        if (!r.ok) throw new Error('Silo HTTP ' + r.status);
        return r.json();
    });
}

function siloAsleep(st) {
    return st && (st.stat === 5 || /in db/i.test(st.stat_string || ''));
}

// ---- torrent and file choice ----

function loadTorrents() {
    return fetch('/api/torrents').then(function (r) { return r.json(); }).then(function (d) {
        var list = (d && d.torrents) || d || [];
        list.sort(function (a, b) { return (a.title || a.name || '').localeCompare(b.title || b.name || ''); });
        $('f-torrent').innerHTML = list.map(function (x) {
            return '<option value="' + esc(x.hash) + '"' + (x.hash === cfg.hash ? ' selected' : '') + '>' +
                esc(x.title || x.name || x.hash) + '</option>';
        }).join('');
        return loadFiles();
    }).catch(function (e) { setMsg(e.message, false); });
}

function loadFiles() {
    var hash = $('f-torrent').value;
    $('f-file').innerHTML = '';
    siloFiles = [];
    if (!hash) return Promise.resolve();
    return siloStatus(hash).then(function (st) {
        siloTorrent = st;
        siloFiles = (st.file_stats || []).slice();
        var best = null;
        siloFiles.forEach(function (f) { if (!best || f.length > best.length) best = f; });
        $('f-file').innerHTML = siloFiles.map(function (f) {
            var sel = (cfg.hash === hash && cfg.fileIdx === f.id) || (!(cfg.hash === hash) && best && f.id === best.id);
            return '<option value="' + f.id + '"' + (sel ? ' selected' : '') + '>' +
                esc((f.name || f.path) + ' — ' + (f.length / 1073741824).toFixed(2) + ' GiB') + '</option>';
        }).join('');
        if (!siloFiles.length) setMsg(t('no_files', 'Silo has no file list for this torrent yet: open it in Silo once.'), false);
    }).catch(function (e) { setMsg(e.message, false); });
}

function siloFile() {
    var idx = parseInt($('f-file').value, 10);
    for (var i = 0; i < siloFiles.length; i++) if (siloFiles[i].id === idx) return siloFiles[i];
    return null;
}

// The same file in TorrServer: same size, then the closest path.
function matchFile(files, want) {
    var same = files.filter(function (f) { return f.length === want.length; });
    if (same.length === 1) return same[0];
    var pool = same.length ? same : files;
    var name = (want.path || '').split('/').pop();
    for (var i = 0; i < pool.length; i++) if ((pool[i].path || '').split('/').pop() === name) return pool[i];
    return same[0] || null;
}

function check() {
    readCfg();
    setMsg(t('checking', 'Checking…'));
    var want = siloFile();
    if (!cfg.hash || !want) { setMsg(t('pick', 'Choose a torrent and a file.'), false); return Promise.resolve(false); }
    var hash = cfg.hash;
    return tsStatus(hash).then(function (st) {
        if (st) return null;
        log(t('ts_adding', 'TorrServer does not have this torrent: adding it.'));
        var link = siloTorrent && /^torrs:\/\//.test(siloTorrent.torrs || '') ? siloTorrent.torrs : 'magnet:?xt=urn:btih:' + hash;
        return tsTorrents({
            action: 'add', link: link, title: (siloTorrent && siloTorrent.title) || '',
            poster: (siloTorrent && siloTorrent.poster) || '', save_to_db: true
        });
    }).then(function () {
        // "get" wakes the torrent and gives its files (metadata may take a moment)
        var tries = 0;
        function poll() {
            return tsTorrents({ action: 'get', hash: hash }).then(function (st) {
                if (st && st.file_stats && st.file_stats.length) return st;
                if (++tries > 30) throw new Error(t('ts_no_meta', 'TorrServer could not get the file list of this torrent in 60 s.'));
                return sleep(2000).then(poll);
            });
        }
        return poll();
    }).then(function (st) {
        tsFile = matchFile(st.file_stats, want);
        if (!tsFile) throw new Error(t('ts_no_file', 'TorrServer has no file of the same size.'));
        return tsTorrents({ action: 'drop', hash: hash });
    }).then(function () {
        setMsg(fmt(t('ready', 'Both servers are ready: file {0} ({1} GiB).'), want.name || want.path,
            (want.length / 1073741824).toFixed(2)), true);
        return true;
    }).catch(function (e) {
        var msg = e.message;
        if (e instanceof TypeError) msg = fmt(t('err_ts_reach', 'TorrServer at {0} does not answer (address, port, or it is stopped).'), cfg.tsUrl);
        setMsg(msg, false);
        return false;
    });
}

// ---- one run ----

function streamUrl(server) {
    if (server === 'silo') return '/api/stream/' + cfg.hash + '/' + cfg.fileIdx;
    var name = (tsFile.path || 'file').split('/').pop();
    return cfg.tsUrl + '/stream/' + encodeURIComponent(name) + '?link=' + cfg.hash + '&index=' + tsFile.id + '&play';
}

// Reads `limit` bytes the way a player does and times it.
function readStream(server, limit, rangeStart) {
    var ctrl = new AbortController();
    currentAbort = ctrl;
    var headers = server === 'ts' ? tsHeaders({}) : {};
    if (rangeStart) headers.Range = 'bytes=' + rangeStart + '-';
    var t0 = performance.now();
    var res = { ttfb: null, tStart: null, tTotal: null, bytes: 0, samples: [[0, 0]], error: null };
    var lastData = t0;
    var watchdog = setInterval(function () {
        var now = performance.now();
        if (now - lastData > STALL_MS || now - t0 > RUN_LIMIT_MS || stopRequested) {
            res.error = stopRequested ? 'stopped' : 'stalled';
            ctrl.abort();
        }
    }, 1000);

    return fetch(streamUrl(server), { headers: headers, signal: ctrl.signal, cache: 'no-store' }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        if (rangeStart && r.status !== 206) throw new Error(t('no_range', 'the server ignored the seek (no HTTP 206)'));
        var reader = r.body.getReader();
        var lastSample = 0;
        function pump() {
            return reader.read().then(function (chunk) {
                if (chunk.done) return;
                var now = performance.now();
                lastData = now;
                if (res.ttfb === null) res.ttfb = now - t0;
                res.bytes += chunk.value.length;
                if (res.tStart === null && res.bytes >= START_MB * MB) res.tStart = now - t0;
                if (now - lastSample >= 250) { res.samples.push([(now - t0) / 1000, res.bytes / MB]); lastSample = now; }
                if (res.bytes >= limit) {
                    res.tTotal = now - t0;
                    res.samples.push([(now - t0) / 1000, res.bytes / MB]);
                    ctrl.abort();
                    return;
                }
                return pump();
            });
        }
        return pump();
    }).catch(function (e) {
        if (res.tTotal === null && !res.error) res.error = e.name === 'AbortError' ? 'aborted' : (e.message || String(e));
        if (res.tTotal === null && e instanceof TypeError && server === 'ts' && rangeStart) res.error = t('no_range_cors', 'the browser blocked the seek request to TorrServer');
    }).then(function () {
        clearInterval(watchdog);
        currentAbort = null;
        if (res.tTotal === null && !res.error) res.error = 'ended early';
        return res;
    });
}

function makeCold(server) {
    if (server === 'ts') {
        return tsTorrents({ action: 'drop', hash: cfg.hash }).then(function () { return sleep(2000); }).then(function () { return true; });
    }
    var t0 = Date.now();
    function poll() {
        if (stopRequested) return Promise.resolve(false);
        return siloStatus(cfg.hash).then(function (st) {
            if (siloAsleep(st)) return true;
            if (Date.now() - t0 > SILO_SLEEP_WAIT_MS) return false;
            return sleep(3000).then(poll);
        });
    }
    return siloStatus(cfg.hash).then(function (st) {
        if (siloAsleep(st)) return true;
        log(t('silo_wait', 'Waiting for Silo to put the torrent to sleep (it does that 60 s after the last read)…'));
        return poll();
    });
}

function startStats(server, run) {
    var t0 = performance.now();
    var busy = false;
    var timer = setInterval(function () {
        if (busy) return;
        busy = true;
        var p = server === 'ts' ? tsStatus(cfg.hash) : siloStatus(cfg.hash);
        p.then(function (st) {
            if (!st) return;
            run.speed.push([(performance.now() - t0) / 1000, (st.download_speed || 0) / MB]);
            run.peers = Math.max(run.peers, st.active_peers || 0);
            run.seeds = Math.max(run.seeds, st.connected_seeders || 0);
        }).catch(function () { /* one missed sample is fine */ }).then(function () { busy = false; });
    }, 1000);
    return function () { clearInterval(timer); };
}

function oneRun(server, round) {
    var run = { server: server, round: round, cold: true, speed: [], peers: 0, seeds: 0, start: null, seek: null };
    log(fmt(t('run_start', 'Round {0}: {1}'), round, NAMES[server]));
    return makeCold(server).then(function (cold) {
        run.cold = cold;
        if (!cold) log(t('not_cold', 'Silo did not put the torrent to sleep in time: this run starts warm.'), 'msg-err');
        var stop = startStats(server, run);
        return readStream(server, cfg.volume * MB, 0).then(function (r) {
            run.start = r;
            if (r.error) log(fmt(t('read_fail', '{0}: reading stopped ({1}).'), NAMES[server], r.error), 'msg-err');
            else log(fmt(t('read_done', '{0}: first byte {1} s, 32 MB in {2} s, {3} MB at {4} MB/s.'), NAMES[server],
                sec(r.ttfb), sec(r.tStart), cfg.volume, mbps(r).toFixed(1)));
            if (!cfg.seek || stopRequested) return;
            var want = siloFile();
            var mid = Math.floor(want.length / 2);
            return readStream(server, cfg.seek * MB, mid).then(function (s) {
                run.seek = s;
                if (s.error) log(fmt(t('seek_fail', '{0}: seek failed ({1}).'), NAMES[server], s.error), 'msg-err');
                else log(fmt(t('seek_done', '{0}: after a seek, first byte {1} s, {2} MB in {3} s.'), NAMES[server],
                    sec(s.ttfb), cfg.seek, sec(s.tTotal)));
            });
        }).then(function () {
            stop();
            runs.push(run);
            if (server === 'ts') return tsTorrents({ action: 'drop', hash: cfg.hash }).catch(function () {});
        });
    });
}

function sec(ms) { return ms == null ? '—' : (ms / 1000).toFixed(2); }

function mbps(r) {
    if (!r || r.tTotal == null || r.ttfb == null) return 0;
    var dt = (r.tTotal - r.ttfb) / 1000;
    return dt > 0 ? (r.bytes / MB) / dt : 0;
}

function run() {
    if (running) return;
    stopRequested = false;
    $('log').innerHTML = '';
    check().then(function (ok) {
        if (!ok) return;
        running = true;
        runs = [];
        $('btn-run').disabled = true;
        $('btn-check').disabled = true;
        $('btn-stop').style.display = '';
        var order = [];
        for (var r = 1; r <= cfg.rounds; r++) {
            var pair = r % 2 ? ['ts', 'silo'] : ['silo', 'ts'];
            order.push([pair[0], r], [pair[1], r]);
        }
        var i = 0;
        function next() {
            if (stopRequested || i >= order.length) return Promise.resolve();
            setProgress(i / order.length * 100);
            var step = order[i++];
            return oneRun(step[0], step[1]).then(next);
        }
        return next().then(function () {
            setProgress(100);
            log(stopRequested ? t('stopped', 'Stopped.') : t('done', 'Done.'), 'msg-ok');
            if (runs.length) render();
        });
    }).catch(function (e) {
        log(e.message || String(e), 'msg-err');
    }).then(function () {
        running = false;
        $('btn-run').disabled = false;
        $('btn-check').disabled = false;
        $('btn-stop').style.display = 'none';
    });
}

// ---- results ----

function avg(list) {
    var v = list.filter(function (x) { return x != null && isFinite(x); });
    if (!v.length) return null;
    return v.reduce(function (a, b) { return a + b; }, 0) / v.length;
}

function speedAvg(run) {
    var v = run.speed.map(function (p) { return p[1]; });
    return avg(v);
}

function speedMax(run) {
    return run.speed.reduce(function (m, p) { return Math.max(m, p[1]); }, 0);
}

var METRICS = [
    { key: 'ttfb', title: ['m_ttfb', 'Start: first byte'], sub: ['m_ttfb_sub', 'from the request to the first data, s'],
        lower: true, unit: 's', get: function (r) { return r.start && r.start.ttfb != null ? r.start.ttfb / 1000 : null; } },
    { key: 'buffer', title: ['m_buffer', 'Ready to play: 32 MB'], sub: ['m_buffer_sub', 'time until the player has its first 32 MB, s'],
        lower: true, unit: 's', get: function (r) { return r.start && r.start.tStart != null ? r.start.tStart / 1000 : null; } },
    { key: 'read', title: ['m_read', 'Reading speed'], sub: ['m_read_sub', 'MB per second delivered to the player'],
        lower: false, unit: 'MB/s', get: function (r) { return r.start && !r.start.error ? mbps(r.start) : null; } },
    { key: 'seek', title: ['m_seek', 'Seek: first byte'], sub: ['m_seek_sub', 'jump to the middle of the file, s'],
        lower: true, unit: 's', get: function (r) { return r.seek && r.seek.ttfb != null && !r.seek.error ? r.seek.ttfb / 1000 : null; } },
    { key: 'seekread', title: ['m_seekread', 'Seek: chunk read'], sub: ['m_seekread_sub', 'time for the MB after the seek, s'],
        lower: true, unit: 's', get: function (r) { return r.seek && !r.seek.error && r.seek.tTotal != null ? r.seek.tTotal / 1000 : null; } },
    { key: 'dlavg', title: ['m_dlavg', 'Torrent speed, average'], sub: ['m_dlavg_sub', 'download speed reported by the server, MB/s'],
        lower: false, unit: 'MB/s', get: function (r) { return speedAvg(r); } },
    { key: 'dlmax', title: ['m_dlmax', 'Torrent speed, peak'], sub: ['m_dlmax_sub', 'highest download speed, MB/s'],
        lower: false, unit: 'MB/s', get: function (r) { return r.speed.length ? speedMax(r) : null; } },
    { key: 'peers', title: ['m_peers', 'Peers'], sub: ['m_peers_sub', 'most active peers at a time'],
        lower: false, unit: '', get: function (r) { return r.peers || null; } }
];

function metricValues(m) {
    var out = {};
    ['ts', 'silo'].forEach(function (s) {
        out[s] = avg(runs.filter(function (r) { return r.server === s; }).map(m.get));
    });
    return out;
}

function winner(m, v) {
    if (v.ts == null || v.silo == null) return null;
    var a = v.ts, b = v.silo;
    var big = Math.max(Math.abs(a), Math.abs(b));
    if (big === 0 || Math.abs(a - b) / big < 0.03) return 'tie';
    return (m.lower ? a < b : a > b) ? 'ts' : 'silo';
}

function num(v, unit) {
    if (v == null) return '—';
    if (unit === '' && Math.round(v) === v) return String(v);
    var s = v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
    return s + (unit ? ' ' + unit : '');
}

function render() {
    $('results').style.display = '';
    var score = { ts: 0, silo: 0 };
    $('metrics').innerHTML = METRICS.map(function (m) {
        var v = metricValues(m);
        var w = winner(m, v);
        if (w === 'ts' || w === 'silo') score[w]++;
        var max = Math.max(v.ts || 0, v.silo || 0) || 1;
        function bar(s) {
            var pct = v[s] == null ? 0 : Math.max(2, v[s] / max * 100);
            return '<div class="bar' + (w === s ? ' win' : '') + '"><span>' + NAMES[s] + (w === s ? ' ✓' : '') + '</span>' +
                '<div class="track"><div class="fill" style="width:' + pct + '%;background:' + COLORS[s] + '"></div></div>' +
                '<span class="val">' + num(v[s], m.unit) + '</span></div>';
        }
        var note = w === 'tie' ? '<div class="sub">' + esc(t('tie', 'a draw (less than 3% apart)')) + '</div>' : '';
        return '<div class="metric"><h3>' + esc(t(m.title[0], m.title[1])) + '</h3><div class="sub">' +
            esc(t(m.sub[0], m.sub[1])) + (m.lower ? ' · ' + esc(t('lower_better', 'lower is better')) : ' · ' +
            esc(t('higher_better', 'higher is better'))) + '</div>' + bar('ts') + bar('silo') + note + '</div>';
    }).join('');
    $('score-ts').textContent = score.ts;
    $('score-silo').textContent = score.silo;

    $('speed-legend').innerHTML = ['ts', 'silo'].map(function (s) {
        return '<span><i style="background:' + COLORS[s] + '"></i>' + NAMES[s] + '</span>';
    }).join('');
    lineChart($('chart-speed'), runs.map(function (r) {
        return { color: COLORS[r.server], dash: r.round > 1, points: r.speed };
    }), 's', 'MB/s');
    lineChart($('chart-read'), runs.filter(function (r) { return r.start; }).map(function (r) {
        return { color: COLORS[r.server], dash: r.round > 1, points: r.start.samples };
    }), 's', 'MB');

    var head = '<tr><th>' + esc(t('col_round', 'Round')) + '</th><th>' + esc(t('col_server', 'Server')) + '</th>' +
        METRICS.map(function (m) { return '<th>' + esc(t(m.title[0], m.title[1])) + '</th>'; }).join('') + '</tr>';
    $('runs').innerHTML = head + runs.map(function (r) {
        return '<tr><td>' + r.round + (r.cold ? '' : ' *') + '</td><td style="color:' + COLORS[r.server] + '">' + NAMES[r.server] + '</td>' +
            METRICS.map(function (m) { return '<td>' + num(m.get(r), m.unit) + '</td>'; }).join('') + '</tr>';
    }).join('');
}

function lineChart(canvas, series, xUnit, yUnit) {
    var box = canvas.parentNode.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(300, box.width) * dpr;
    canvas.height = box.height * dpr;
    var ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    var W = canvas.width / dpr, H = canvas.height / dpr;
    var pad = { l: 46, r: 12, t: 10, b: 28 };
    var text = getComputedStyle(document.body).color || '#888';
    var xMax = 1, yMax = 1;
    series.forEach(function (s) {
        s.points.forEach(function (p) { xMax = Math.max(xMax, p[0]); yMax = Math.max(yMax, p[1]); });
    });
    yMax = niceMax(yMax);
    xMax = niceMax(xMax);
    var X = function (x) { return pad.l + x / xMax * (W - pad.l - pad.r); };
    var Y = function (y) { return H - pad.b - y / yMax * (H - pad.t - pad.b); };
    ctx.clearRect(0, 0, W, H);
    ctx.font = '11px system-ui, sans-serif';
    ctx.fillStyle = text;
    ctx.strokeStyle = 'rgba(127,127,127,.25)';
    ctx.lineWidth = 1;
    for (var i = 0; i <= 4; i++) {
        var yv = yMax * i / 4, y = Y(yv);
        ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
        ctx.textAlign = 'right'; ctx.fillText(num(yv, ''), pad.l - 6, y + 4);
        var xv = xMax * i / 4;
        ctx.textAlign = i === 4 ? 'right' : i === 0 ? 'left' : 'center';
        ctx.fillText(num(xv, '') + ' ' + xUnit, i === 4 ? W - pad.r : X(xv), H - 8);
    }
    ctx.save(); ctx.translate(11, pad.t + 40); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'right'; ctx.fillText(yUnit, 0, 0); ctx.restore();
    series.forEach(function (s) {
        if (!s.points.length) return;
        ctx.beginPath();
        ctx.setLineDash(s.dash ? [6, 4] : []);
        ctx.strokeStyle = s.color;
        ctx.lineWidth = 2;
        s.points.forEach(function (p, j) { j ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1])); });
        ctx.stroke();
    });
    ctx.setLineDash([]);
}

function niceMax(v) {
    var p = Math.pow(10, Math.floor(Math.log10(v)));
    var n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function copyText() {
    var lines = ['TorrServer vs Silo — ' + ((siloTorrent && siloTorrent.title) || cfg.hash),
        fmt(t('copy_setup', 'Read {0} MB from the start, {1} MB after a seek, {2} round(s).'), cfg.volume, cfg.seek, cfg.rounds),
        '', '| ' + t('col_measure', 'Measure') + ' | TorrServer | Silo |', '|---|---|---|'];
    METRICS.forEach(function (m) {
        var v = metricValues(m), w = winner(m, v);
        lines.push('| ' + t(m.title[0], m.title[1]) + ' | ' + num(v.ts, m.unit) + (w === 'ts' ? ' ✓' : '') +
            ' | ' + num(v.silo, m.unit) + (w === 'silo' ? ' ✓' : '') + ' |');
    });
    lines.push('', 'Score: TorrServer ' + $('score-ts').textContent + ' : ' + $('score-silo').textContent + ' Silo');
    var s = lines.join('\n');
    var done = function () { setMsg(t('copied', 'Copied.'), true); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(s).then(done);
    else {
        var ta = document.createElement('textarea');
        ta.value = s; document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); done(); } catch (e) { /* nothing */ }
        ta.remove();
    }
}

// ---- wiring ----

$('f-torrent').addEventListener('change', loadFiles);
$('btn-check').addEventListener('click', function () { $('log').innerHTML = ''; check(); });
$('btn-run').addEventListener('click', run);
$('btn-stop').addEventListener('click', function () {
    stopRequested = true;
    if (currentAbort) currentAbort.abort();
});
$('btn-copy').addEventListener('click', copyText);
window.addEventListener('resize', function () { if (runs.length && !running) render(); });

if (typeof i18n !== 'undefined' && i18n.apply) i18n.apply();
loadCfg();
loadTorrents();
