// ===============================================================
// torrserver-import: takes the torrent list of TorrServer (MatriX)
// into Silo.
// ===============================================================
//
// TorrServer API: POST <url>/torrents {"action":"list"} returns all its
// torrents (hash, title, poster, category, timestamp). Each one that Silo does
// not have yet is added with ts.torrent.add(hash, {title, poster, category});
// Silo then fetches the metadata itself.
//
// Two ways to import:
//   * "Import now" on the plugin page: adds every TorrServer torrent that is
//     missing in Silo, also ones deleted in Silo earlier.
//   * Automatic scan every N minutes: adds only torrents it has not seen
//     before, so a torrent deleted in Silo is not added back.
//
// Silo plugins have no timers. The automatic scan runs at Silo start and
// whenever something calls POST /plugins/torrserver-import/api/tick (Silo DSM
// does this every minute); the plugin itself decides whether the interval is
// over, so frequent calls are harmless.
//
// ES5 only: Silo runs plugins in goja.

var OWNER_RANK = 100;
var HTTP_TIMEOUT_MS = 15000;

var DEFAULT_SETTINGS = {
    url: "http://127.0.0.1:8090",
    username: "",
    password: "",
    // Off until the owner turns it on: the plugin is built into Silo DSM and
    // must not start copying torrents by itself.
    auto: false,
    interval_min: 15
};

// ---------------------------------------------------------------
// Storage
// ---------------------------------------------------------------

function loadJSON(key, fallback) {
    var raw = ts.storage.get(key);
    if (!raw) return fallback;
    try { return JSON.parse(raw); } catch (e) { return fallback; }
}

function saveJSON(key, value) {
    ts.storage.set(key, JSON.stringify(value));
}

function getSettings() {
    var s = loadJSON("settings", {});
    var out = {};
    for (var k in DEFAULT_SETTINGS) {
        out[k] = (s && s[k] !== undefined && s[k] !== null) ? s[k] : DEFAULT_SETTINGS[k];
    }
    return out;
}

function getState() {
    return loadJSON("state", {
        last_run: 0, last_mode: "", ok: null, error: "",
        found: 0, added: 0, skipped: 0, failed: 0
    });
}

function getSeen() {
    var list = loadJSON("seen", []);
    var map = {};
    for (var i = 0; i < list.length; i++) map[list[i]] = true;
    return map;
}

function saveSeen(map) {
    var list = [];
    for (var h in map) if (map.hasOwnProperty(h)) list.push(h);
    saveJSON("seen", list);
}

// ---------------------------------------------------------------
// TorrServer
// ---------------------------------------------------------------

function normalizeUrl(url) {
    url = String(url || "").replace(/\s+/g, "");
    if (url && !/^https?:\/\//i.test(url)) url = "http://" + url;
    return url.replace(/\/+$/, "");
}

// Returns {ok, list} or {ok: false, code, error}. The page translates code;
// error is the English text with details (also for the log).
function fail(code, error) {
    return { ok: false, code: code, error: error };
}

function fetchTorrServerList(settings) {
    var url = normalizeUrl(settings.url);
    if (!url) return fail("no_url", "TorrServer address is not set");

    var headers = { "Content-Type": "application/json" };
    if (settings.username) {
        headers["Authorization"] = "Basic " +
            ts.crypto.base64encode(settings.username + ":" + (settings.password || ""));
    }

    var resp;
    try {
        resp = ts.http.post(url + "/torrents", { action: "list" },
            { headers: headers, timeout: HTTP_TIMEOUT_MS });
    } catch (e) {
        return fail("unreachable", "TorrServer is not reachable: " + String(e));
    }

    if (resp.status === 401) return fail("unauthorized", "TorrServer rejected the login (401)");
    if (!resp.ok) return fail("http", "TorrServer answered HTTP " + resp.status);

    var list;
    try { list = resp.json(); } catch (e) {
        return fail("bad_answer", "TorrServer answered something that is not a torrent list");
    }
    if (!list) list = [];
    if (!(list instanceof Array)) {
        return fail("bad_answer", "TorrServer answered something that is not a torrent list");
    }
    return { ok: true, list: list };
}

// ---------------------------------------------------------------
// Import
// ---------------------------------------------------------------

// ts.torrent.list() hands over Go structs: in Silo.1 their fields keep the Go
// names (Hash), not the JSON names (hash). Both are read.
function siloHashes() {
    var map = {};
    var list = ts.torrent.list() || [];
    for (var i = 0; i < list.length; i++) {
        var t = list[i] || {};
        var h = String(t.Hash || t.hash || "").toLowerCase();
        if (h) map[h] = true;
    }
    return map;
}

// mode: "manual" adds everything missing in Silo; "auto" adds only torrents
// it has not seen before.
function runImport(mode) {
    var settings = getSettings();
    var state = {
        last_run: Date.now(), last_mode: mode, ok: false, code: "", error: "",
        found: 0, added: 0, skipped: 0, failed: 0
    };

    var res = fetchTorrServerList(settings);
    if (!res.ok) {
        state.code = res.code;
        state.error = res.error;
        saveJSON("state", state);
        console.log("[TS Import] " + mode + ": " + res.error);
        return state;
    }

    var items = res.list.slice().sort(function(a, b) {
        return (a.timestamp || 0) - (b.timestamp || 0);
    });
    var have = siloHashes();
    var seen = getSeen();
    var errors = [];
    state.found = items.length;

    for (var i = 0; i < items.length; i++) {
        var it = items[i] || {};
        var hash = String(it.hash || "").toLowerCase();
        if (!/^[0-9a-f]{40}$/.test(hash)) { state.skipped++; continue; }

        if (have[hash] || (mode === "auto" && seen[hash])) {
            seen[hash] = true;
            state.skipped++;
            continue;
        }

        try {
            ts.torrent.add(hash, {
                title: String(it.title || it.name || ""),
                poster: String(it.poster || ""),
                category: String(it.category || "")
            });
            have[hash] = true;
            seen[hash] = true;
            state.added++;
        } catch (e) {
            state.failed++;
            if (errors.length < 3) errors.push((it.title || hash) + ": " + String(e));
        }
    }

    saveSeen(seen);
    state.ok = state.failed === 0;
    state.code = errors.length ? "add_failed" : "";
    state.error = errors.join("; ");
    saveJSON("state", state);
    console.log("[TS Import] " + mode + ": found " + state.found + ", added " + state.added +
        ", skipped " + state.skipped + ", failed " + state.failed);
    return state;
}

function autoDue() {
    var s = getSettings();
    if (!s.auto) return false;
    var interval = Math.max(1, parseInt(s.interval_min, 10) || DEFAULT_SETTINGS.interval_min);
    var last = getState().last_run || 0;
    return Date.now() - last >= interval * 60 * 1000;
}

// ---------------------------------------------------------------
// HTTP API
// ---------------------------------------------------------------

function isOwner(req) {
    return req.user && req.user.rank >= OWNER_RANK;
}

function forbidden(res) {
    return res.status(403).json({ error: "owner access required" });
}

function publicSettings() {
    var s = getSettings();
    return {
        url: s.url,
        username: s.username,
        has_password: !!s.password,
        auto: !!s.auto,
        interval_min: s.interval_min
    };
}

ts.web.get("/api/status", function(req, res) {
    if (!isOwner(req)) return forbidden(res);
    var st = getState();
    var s = getSettings();
    var next = 0;
    if (s.auto) {
        next = (st.last_run || 0) + Math.max(1, parseInt(s.interval_min, 10) || 15) * 60 * 1000;
    }
    return { settings: publicSettings(), state: st, next_run: next, seen: Object.keys(getSeen()).length };
});

ts.web.post("/api/settings", function(req, res) {
    if (!isOwner(req)) return forbidden(res);
    var b = req.body || {};
    var cur = getSettings();

    var interval = parseInt(b.interval_min, 10);
    if (isNaN(interval) || interval < 1 || interval > 1440) {
        return res.status(400).json({ error: "interval_invalid" });
    }
    var url = normalizeUrl(b.url);
    if (!/^https?:\/\/[^\s\/]+/.test(url)) {
        return res.status(400).json({ error: "url_invalid" });
    }

    var next = {
        url: url,
        username: String(b.username || ""),
        // An empty password field keeps the saved password; "clear_password"
        // removes it.
        password: b.clear_password ? "" : (b.password ? String(b.password) : cur.password),
        auto: !!b.auto,
        interval_min: interval
    };
    saveJSON("settings", next);
    return { status: "ok", settings: publicSettings() };
});

ts.web.post("/api/test", function(req, res) {
    if (!isOwner(req)) return forbidden(res);
    var r = fetchTorrServerList(getSettings());
    if (!r.ok) return { ok: false, code: r.code, error: r.error };
    return { ok: true, count: r.list.length };
});

ts.web.post("/api/import", function(req, res) {
    if (!isOwner(req)) return forbidden(res);
    return runImport("manual");
});

// Wake-up for the automatic scan (no login: it only runs the scan with the
// saved settings, and at most once per interval).
ts.web.post("/api/tick", function(req, res) {
    if (!autoDue()) return { ran: false };
    var st = runImport("auto");
    return { ran: true, added: st.added, ok: st.ok };
});

// ---------------------------------------------------------------
// Page and start-up
// ---------------------------------------------------------------

ts.web.staticFile("/", "index.html");
ts.web.staticFile("/index.html", "index.html");
ts.web.staticDir("/js", "js");
ts.web.staticDir("/img", "img");

ts.bus.on("system:boot", function() {
    try {
        if (autoDue()) runImport("auto");
    } catch (e) {
        console.log("[TS Import] start-up scan failed: " + String(e));
    }
});

console.log("[TS Import] ready");
