// ===============================================================
// plex-scan: tells Plex to scan its libraries when torrents come and
// go in Silo.
// ===============================================================
//
// Meant for a Plex library on the folder where Silo's FUSE plugin mounts the
// torrent library. Plex does not notice changes in a FUSE folder by itself,
// so without this its library only changes at a scheduled scan.
//
// When it scans:
//   * a torrent appears: on "torrent:metadata" for a hash it has not seen
//     before (its files are known then, so they are in the FUSE folder).
//     Silo sends this event again whenever a torrent wakes up; those repeats
//     are recognised and ignored;
//   * a torrent is removed ("user:torrent:removed");
//   * a torrent changes, e.g. moves to another category ("user:torrent:updated").
//
// Plex is asked at most once every MIN_GAP_MS. A change inside that time is
// kept as "pending" and sent by /api/tick, which Silo DSM calls every minute
// (Silo plugins have no timers).
//
// Plex API: GET /library/sections lists the libraries,
// GET /library/sections/<key>/refresh starts a scan of one library (Plex only
// rereads folders that changed). Authentication: X-Plex-Token.
//
// ES5 only: Silo runs plugins in goja.

var OWNER_RANK = 100;
var MIN_GAP_MS = 30 * 1000;
var HTTP_TIMEOUT_MS = 5000;

var DEFAULT_SETTINGS = {
    enabled: false,          // off until the owner has entered Plex and saved
    url: "http://127.0.0.1:32400",
    token: "",
    sections: [],            // library keys; empty = every library
    on_add: true,
    on_remove: true
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
    var s = loadJSON("settings", {}) || {};
    var out = {};
    for (var k in DEFAULT_SETTINGS) {
        out[k] = (s[k] !== undefined && s[k] !== null) ? s[k] : DEFAULT_SETTINGS[k];
    }
    if (!(out.sections instanceof Array)) out.sections = [];
    return out;
}

function getState() {
    return loadJSON("state", {
        last_scan: 0, reason: "", ok: null, code: "", error: "",
        scanned: [], pending: false, pending_reason: ""
    });
}

// Hashes whose files Plex has been told about.
function getKnown() {
    var list = loadJSON("known", null);
    var map = {};
    if (list) for (var i = 0; i < list.length; i++) map[list[i]] = true;
    return { map: map, loaded: !!list };
}

function saveKnown(map) {
    var list = [];
    for (var h in map) if (map.hasOwnProperty(h)) list.push(h);
    saveJSON("known", list);
}

// ---------------------------------------------------------------
// Plex
// ---------------------------------------------------------------

function normalizeUrl(url) {
    url = String(url || "").replace(/\s+/g, "");
    if (url && !/^https?:\/\//i.test(url)) url = "http://" + url;
    return url.replace(/\/+$/, "");
}

function fail(code, error) {
    return { ok: false, code: code, error: error };
}

// GET <url><path> with the token; returns {ok, data} or {ok:false, code, error}.
function plexGet(settings, path) {
    var url = normalizeUrl(settings.url);
    if (!url) return fail("no_url", "Plex address is not set");
    if (!settings.token) return fail("no_token", "Plex token is not set");

    var full = url + path + (path.indexOf("?") < 0 ? "?" : "&") +
        "X-Plex-Token=" + encodeURIComponent(settings.token);
    var resp;
    try {
        resp = ts.http.get(full, { headers: { "Accept": "application/json" }, timeout: HTTP_TIMEOUT_MS });
    } catch (e) {
        // The token is part of the address: keep it out of messages.
        return fail("unreachable", "Plex is not reachable: " +
            String(e).split(settings.token).join("***"));
    }
    if (resp.status === 401) return fail("unauthorized", "Plex rejected the token (401)");
    if (!resp.ok) return fail("http", "Plex answered HTTP " + resp.status);
    var data = null;
    if (resp.body) {
        try { data = resp.json(); } catch (e) { data = null; }
    }
    return { ok: true, data: data };
}

// Libraries of the Plex server: [{key, title, type, paths}].
function listSections(settings) {
    var r = plexGet(settings, "/library/sections");
    if (!r.ok) return r;
    var dirs = r.data && r.data.MediaContainer && r.data.MediaContainer.Directory;
    if (!(dirs instanceof Array)) return fail("bad_answer", "The address does not answer like Plex");
    var out = [];
    for (var i = 0; i < dirs.length; i++) {
        var d = dirs[i] || {};
        var paths = [];
        var loc = d.Location instanceof Array ? d.Location : [];
        for (var j = 0; j < loc.length; j++) if (loc[j] && loc[j].path) paths.push(String(loc[j].path));
        out.push({ key: String(d.key), title: String(d.title || d.key), type: String(d.type || ""), paths: paths });
    }
    return { ok: true, sections: out };
}

// Starts a scan of the chosen libraries (all when none is chosen).
function scan(reason) {
    var settings = getSettings();
    var state = getState();
    state.last_scan = Date.now();
    state.reason = reason;
    state.pending = false;
    state.pending_reason = "";
    state.scanned = [];
    state.ok = false;
    state.code = "";
    state.error = "";

    var listed = listSections(settings);
    if (!listed.ok) {
        state.code = listed.code;
        state.error = listed.error;
        saveJSON("state", state);
        console.log("[Plex] scan (" + reason + "): " + listed.error);
        return state;
    }

    var wanted = settings.sections;
    var errors = [];
    for (var i = 0; i < listed.sections.length; i++) {
        var sec = listed.sections[i];
        if (wanted.length && wanted.indexOf(sec.key) < 0) continue;
        var r = plexGet(settings, "/library/sections/" + encodeURIComponent(sec.key) + "/refresh");
        if (r.ok) state.scanned.push(sec.title);
        else errors.push(sec.title + ": " + r.error);
    }

    if (!state.scanned.length && !errors.length) {
        state.code = "no_sections";
        state.error = "None of the chosen Plex libraries exists any more";
    } else if (errors.length) {
        state.code = "scan_failed";
        state.error = errors.join("; ");
    }
    state.ok = !state.code;
    saveJSON("state", state);
    console.log("[Plex] scan (" + reason + "): " + (state.ok ? state.scanned.join(", ") : state.error));
    return state;
}

// A change happened: scan now, or later when Plex was asked a moment ago.
function request(reason) {
    var settings = getSettings();
    if (!settings.enabled) return;
    var state = getState();
    if (Date.now() - (state.last_scan || 0) < MIN_GAP_MS) {
        if (!state.pending) {
            state.pending = true;
            state.pending_reason = reason;
            saveJSON("state", state);
        }
        return;
    }
    scan(reason);
}

// ---------------------------------------------------------------
// Silo events
// ---------------------------------------------------------------

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

function hashOf(data) {
    if (!data) return "";
    return String(data.hash || data.Hash || "").toLowerCase();
}

function guarded(name, fn) {
    return function (data) {
        try { fn(data); } catch (e) { console.log("[Plex] " + name + " failed: " + String(e)); }
    };
}

ts.bus.on("system:boot", guarded("start-up", function () {
    // The first start knows every torrent already in Silo: those are not news
    // for Plex.
    if (!getKnown().loaded) saveKnown(siloHashes());
}));

ts.bus.on("torrent:metadata", guarded("metadata", function (data) {
    var hash = hashOf(data);
    if (!hash) return;
    var known = getKnown();
    if (known.map[hash]) return;          // a torrent waking up again
    known.map[hash] = true;
    saveKnown(known.map);
    if (getSettings().on_add) request("added");
}));

ts.bus.on("user:torrent:removed", guarded("removed", function (data) {
    var hash = hashOf(data);
    var known = getKnown();
    if (hash && known.map[hash]) {
        delete known.map[hash];
        saveKnown(known.map);
    }
    if (getSettings().on_remove) request("removed");
}));

ts.bus.on("user:torrent:updated", guarded("updated", function () {
    if (getSettings().on_add) request("changed");
}));

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
        enabled: !!s.enabled, url: s.url, has_token: !!s.token,
        sections: s.sections, on_add: !!s.on_add, on_remove: !!s.on_remove
    };
}

ts.web.get("/api/status", function (req, res) {
    if (!isOwner(req)) return forbidden(res);
    return { settings: publicSettings(), state: getState(), gap_sec: MIN_GAP_MS / 1000 };
});

ts.web.post("/api/settings", function (req, res) {
    if (!isOwner(req)) return forbidden(res);
    var b = req.body || {};
    var cur = getSettings();
    var url = normalizeUrl(b.url);
    if (!/^https?:\/\/[^\s\/]+/.test(url)) return res.status(400).json({ error: "url_invalid" });
    var sections = [];
    if (b.sections instanceof Array) {
        for (var i = 0; i < b.sections.length; i++) sections.push(String(b.sections[i]));
    }
    saveJSON("settings", {
        enabled: !!b.enabled,
        url: url,
        // An empty token field keeps the saved token.
        token: b.token ? String(b.token).replace(/\s+/g, "") : cur.token,
        sections: sections,
        on_add: !!b.on_add,
        on_remove: !!b.on_remove
    });
    return { status: "ok", settings: publicSettings() };
});

// Libraries for the checkboxes on the page (uses the saved settings).
ts.web.post("/api/sections", function (req, res) {
    if (!isOwner(req)) return forbidden(res);
    var r = listSections(getSettings());
    if (!r.ok) return { ok: false, code: r.code, error: r.error };
    return { ok: true, sections: r.sections };
});

ts.web.post("/api/scan", function (req, res) {
    if (!isOwner(req)) return forbidden(res);
    return scan("manual");
});

// Wake-up for a pending scan (no login: it only sends what is already due).
ts.web.post("/api/tick", function (req, res) {
    var st = getState();
    if (!st.pending || !getSettings().enabled) return { ran: false };
    if (Date.now() - (st.last_scan || 0) < MIN_GAP_MS) return { ran: false };
    var done = scan(st.pending_reason || "pending");
    return { ran: true, ok: done.ok };
});

ts.web.staticFile("/", "index.html");
ts.web.staticFile("/index.html", "index.html");
ts.web.staticDir("/js", "js");
ts.web.staticDir("/img", "img");

console.log("[Plex] ready");
