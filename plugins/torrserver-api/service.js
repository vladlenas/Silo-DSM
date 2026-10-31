// TorrServer API: Silo as a TorrServer for apps.
//
// Apps such as Lampa or Seena talk to a TorrServer with a handful of calls
// (Lampa: src/interaction/torserver.js in lampa-source): POST /settings
// {action:get} to check the server (it wants CacheSize in the answer), POST
// /torrents {action:add|get|list|rem|drop}, POST /viewed {action:list|set},
// POST /cache {action:get}, GET /stream/<name>?link=<hash>&index=<id>&play
// and GET /play/<hash>/<id>. Silo DSM sends these root paths of Silo's port
// here (overlay/core/silo_dsm_fixes.go), so the app only needs
// "http://<NAS>:8095" as its TorrServer address.
//
// Differences from TorrServer that this plugin hides:
// - file ids: TorrServer counts files from 1, Silo from 0;
// - streams: /stream answers with a redirect to Silo's own /api/stream;
// - Silo keeps no "data" or timecodes, so timecodes are kept here.

var PREFIX = "/plugins/torrserver-api";
var VERSION = "MatriX.136 (Silo)";
var RECENT_MAX = 20;

// ---- storage ----

function loadJSON(key, def) {
    var raw = ts.storage.get(key);
    if (!raw) return def;
    try { return JSON.parse(raw); } catch (e) { return def; }
}

function saveJSON(key, value) { ts.storage.set(key, JSON.stringify(value)); }

function getSettings() {
    var s = loadJSON("settings", {}) || {};
    return {
        user_id: s.user_id || "owner",
        login: s.login || "",
        pass_hash: s.pass_hash || "",
        token: s.token || ""
    };
}

function remember(entry) {
    var list = loadJSON("recent", []) || [];
    entry.at = Date.now();
    list.unshift(entry);
    if (list.length > RECENT_MAX) list.length = RECENT_MAX;
    saveJSON("recent", list);
}

// ---- helpers ----

function pick(o, a, b) {
    if (!o) return undefined;
    return o[a] !== undefined ? o[a] : o[b];
}

function cors(res) {
    res.header("Access-Control-Allow-Origin", "*")
        .header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, Accept, Range")
        .header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
        .header("Access-Control-Allow-Private-Network", "true");
    return res;
}

// Lampa posts a JSON string, often with the form content type, so the raw
// body is read first.
function body(req) {
    var raw = String(req.raw || "").trim();
    if (raw.charAt(0) === "{") {
        try { return JSON.parse(raw); } catch (e) { /* fall through */ }
    }
    var b = req.body;
    if (b && typeof b === "object" && !(b instanceof Array)) {
        if (b.action !== undefined) return b;
        for (var k in b) {
            if (b.hasOwnProperty(k) && k.charAt(0) === "{") {
                try { return JSON.parse(k); } catch (e) { /* not it */ }
            }
        }
        return b;
    }
    return {};
}

// Silo panics on a malformed hash (NewHashFromHex), so only valid ones go in.
function validHash(h) { return /^[0-9a-f]{40}$/.test(h); }

function hashOf(link) {
    var s = String(link || "").trim();
    var m = /xt=urn:btih:([0-9a-fA-F]{40})/.exec(s);
    if (m) return m[1].toLowerCase();
    if (/^[0-9a-fA-F]{40}$/.test(s)) return s.toLowerCase();
    return "";
}

function sha256(s) { return ts.crypto.sha256(s); }

// Lampa sends Basic auth only when "authorization" is on in its settings.
function allowed(req) {
    var s = getSettings();
    if (!s.pass_hash) return true;
    var h = String(req.headers["authorization"] || "");
    if (h.indexOf("Basic ") !== 0) return false;
    var raw;
    try { raw = ts.crypto.base64decode(h.substring(6).trim()); } catch (e) { return false; }
    var i = raw.indexOf(":");
    if (i < 0) return false;
    if (s.login && raw.substring(0, i) !== s.login) return false;
    return ts.crypto.constantTimeCompare(sha256(raw.substring(i + 1)), s.pass_hash);
}

function deny(res) {
    cors(res).status(401).header("WWW-Authenticate", 'Basic realm="Silo"').text("");
}

function isOwner(req) { return req.user && req.user.rank >= 100; }

// Silo status (Go field names) -> TorrServer status (JSON names, files from 1).
function toTS(st) {
    if (!st) return null;
    var files = pick(st, "FileStats", "file_stats") || [];
    var out = [];
    for (var i = 0; i < files.length; i++) {
        var f = files[i];
        out.push({ id: Number(pick(f, "Id", "id")) + 1, path: pick(f, "Path", "path"), length: pick(f, "Length", "length") });
    }
    var r = {
        title: pick(st, "Title", "title") || "",
        name: pick(st, "Name", "name") || "",
        category: pick(st, "Category", "category") || "",
        poster: pick(st, "Poster", "poster") || "",
        data: "",
        timestamp: pick(st, "Timestamp", "timestamp") || 0,
        hash: String(pick(st, "Hash", "hash") || "").toLowerCase(),
        stat: pick(st, "Stat", "stat") || 0,
        stat_string: pick(st, "StatString", "stat_string") || "",
        loaded_size: pick(st, "LoadedSize", "loaded_size") || 0,
        torrent_size: pick(st, "TorrentSize", "torrent_size") || 0,
        preloaded_bytes: pick(st, "PreloadedBytes", "preloaded_bytes") || 0,
        preload_size: pick(st, "PreloadSize", "preload_size") || 0,
        download_speed: pick(st, "DownloadSpeed", "download_speed") || 0,
        upload_speed: pick(st, "UploadSpeed", "upload_speed") || 0,
        total_peers: pick(st, "TotalPeers", "total_peers") || 0,
        pending_peers: pick(st, "PendingPeers", "pending_peers") || 0,
        active_peers: pick(st, "ActivePeers", "active_peers") || 0,
        connected_seeders: pick(st, "ConnectedSeeders", "connected_seeders") || 0,
        half_open_peers: pick(st, "HalfOpenPeers", "half_open_peers") || 0,
        bytes_written: pick(st, "BytesWritten", "bytes_written") || 0,
        bytes_read: pick(st, "BytesRead", "bytes_read") || 0
    };
    if (out.length) r.file_stats = out;
    return r;
}

function getStatus(hash) {
    if (!validHash(hash)) return null;
    try { return toTS(ts.torrent.get(hash, getSettings().user_id)); } catch (e) { return null; }
}

// ---- TorrServer API ----

function handleOptions(req, res) { cors(res).status(204).text(""); }

function handleEcho(req, res) {
    if (!allowed(req)) return deny(res);
    cors(res).text(VERSION);
}

// Lampa only checks that CacheSize is there ("is it a MatriX TorrServer").
function handleSettings(req, res) {
    if (!allowed(req)) return deny(res);
    cors(res).json({
        CacheSize: 134217728, ReaderReadAHead: 95, PreloadCache: 50, UseDisk: false,
        EnableIPv6: false, DisableTCP: false, DisableUTP: false, DisableUPNP: false,
        DisableDHT: false, DisablePEX: false, DisableUpload: false, ConnectionsLimit: 25,
        TorrentDisconnectTimeout: 60, ForceEncrypt: false, RetrackersMode: 1
    });
}

function handleTorrents(req, res) {
    if (!allowed(req)) return deny(res);
    var b = body(req);
    var s = getSettings();
    var action = String(b.action || "");
    cors(res);

    if (action === "add") {
        var link = String(b.link || "").trim();
        var hash = hashOf(link);
        var input = /^magnet:/i.test(link) ? link : hash;
        if (!input) {
            remember({ ok: false, title: b.title || "", error: "unsupported link" });
            console.log("[TS API] cannot add this link (not magnet or hash): " + link.substring(0, 80));
            return res.status(400).json({ error: "Silo: only magnet links and info hashes can be added" });
        }
        try {
            var st = ts.torrent.add(input, {
                title: String(b.title || ""), poster: String(b.poster || ""),
                category: String(b.category || ""), save_to_db: true, user_id: s.user_id
            });
            var out = toTS(st) || { hash: hash };
            if (!out.hash) out.hash = hash;
            remember({ ok: true, title: b.title || out.title || hash, hash: out.hash });
            console.log("[TS API] added " + out.hash + " " + String(b.title || ""));
            return res.json(out);
        } catch (e) {
            remember({ ok: false, title: b.title || hash, error: String(e) });
            return res.status(500).json({ error: String(e) });
        }
    }

    if (action === "get") {
        var g = getStatus(String(b.hash || "").toLowerCase());
        if (!g) return res.status(404).json({ error: "torrent not found" });
        return res.json(g);
    }

    if (action === "list") {
        var list = [];
        try { list = ts.torrent.list(s.user_id) || []; } catch (e) { list = []; }
        var out2 = [];
        for (var i = 0; i < list.length; i++) out2.push(toTS(list[i]));
        return res.json(out2);
    }

    if (action === "rem") {
        if (!validHash(String(b.hash || "").toLowerCase())) return res.text("");
        try { ts.torrent.remove(String(b.hash || "").toLowerCase(), s.user_id); } catch (e) { /* already gone */ }
        return res.text("");
    }

    // drop (close for now), set (rename), wipe: Silo manages these itself
    return res.text("");
}

function viewedKey(hash) { return "viewed:" + String(hash || "").toLowerCase(); }

function handleViewed(req, res) {
    if (!allowed(req)) return deny(res);
    var b = body(req);
    var hash = String(b.hash || "").toLowerCase();
    cors(res);

    if (!validHash(hash)) return res.json([]);
    if (b.action === "set") {
        var map = loadJSON(viewedKey(hash), {}) || {};
        var idx = Number(b.file_index) || 0;
        map[idx] = Number(b.timecode) || 0;
        saveJSON(viewedKey(hash), map);
        if (idx >= 1) {
            try { ts.torrent.setViewed(hash, idx - 1, true, getSettings().user_id); } catch (e) { /* not in Silo */ }
        }
        return res.text("");
    }
    if (b.action === "rem") {
        ts.storage.rem(viewedKey(hash));
        return res.text("");
    }
    // list
    var out = [];
    var m = loadJSON(viewedKey(hash), {}) || {};
    for (var k in m) if (m.hasOwnProperty(k)) out.push({ hash: hash, file_index: Number(k), timecode: m[k] });
    return res.json(out);
}

function handleCache(req, res) {
    if (!allowed(req)) return deny(res);
    var b = body(req);
    var st = getStatus(String(b.hash || "").toLowerCase());
    cors(res);
    if (!st) return res.json({});
    return res.json({
        Hash: st.hash, Capacity: st.preload_size || 0, Filled: st.preloaded_bytes || 0,
        PiecesLength: 0, PiecesCount: 0, Torrent: st, Pieces: {}, Readers: []
    });
}

// /stream/<name>?link=<hash or magnet>&index=<1-based>&play|preload|stat|m3u
function handleStream(req, res) {
    if (!allowed(req)) return deny(res);
    var q = req.query || {};
    var link = String(q.link || "");
    var hash = hashOf(link);
    cors(res);
    if (!hash) return res.status(400).text("link is not a hash or magnet");

    var s = getSettings();
    var st = getStatus(hash);
    if (!st && /^magnet:/i.test(link)) {
        try { st = toTS(ts.torrent.add(link, { save_to_db: true, user_id: s.user_id })); } catch (e) { st = null; }
    }
    if (!st) return res.status(404).text("torrent not found in Silo");

    if (q.stat !== undefined) return res.json(st);

    var index = parseInt(q.index, 10);
    if (!(index >= 1)) {
        var files = st.file_stats || [];
        if (files.length === 1) index = files[0].id;
        else return res.status(400).text("index is required");
    }
    var target = "/api/stream/" + hash + "/" + (index - 1) + (s.token ? "?token=" + encodeURIComponent(s.token) : "");
    console.log("[TS API] play " + hash + " file " + index);
    return res.status(302).header("Location", target).text("");
}

// /play/<hash>/<id>: TorrServer's short play address (id counts from 1).
function handlePlay(req, res) {
    if (!allowed(req)) return deny(res);
    var parts = String(req.path || "").split("/play/")[1] || "";
    var bits = parts.split("/");
    req.query = req.query || {};
    req.query.link = bits[0];
    if (bits[1]) req.query.index = bits[1];
    req.query.play = "";
    return handleStream(req, res);
}

// ---- page API (Silo owner only) ----

function publicSettings() {
    var s = getSettings();
    return { user_id: s.user_id, login: s.login, has_password: !!s.pass_hash, has_token: !!s.token };
}

ts.web.get("/api/status", function (req, res) {
    if (!isOwner(req)) return res.status(403).json({ error: "forbidden" });
    var users = [];
    try { users = ts.users.list() || []; } catch (e) { users = []; }
    return { settings: publicSettings(), users: users, recent: loadJSON("recent", []) || [], prefix: PREFIX };
});

ts.web.post("/api/settings", function (req, res) {
    if (!isOwner(req)) return res.status(403).json({ error: "forbidden" });
    var b = req.body || {};
    var cur = getSettings();
    var next = {
        user_id: String(b.user_id || "owner"),
        login: String(b.login || "").trim(),
        pass_hash: cur.pass_hash,
        token: cur.token
    };
    if (b.clear_password) next.pass_hash = "";
    else if (b.password) next.pass_hash = sha256(String(b.password));
    if (b.clear_token) next.token = "";
    else if (b.token) next.token = String(b.token).trim();
    saveJSON("settings", next);
    return { status: "ok", settings: publicSettings() };
});

var API = ["/echo", "/settings", "/torrents", "/viewed", "/cache", "/stream", "/stream/*path", "/play/*path"];
for (var i = 0; i < API.length; i++) ts.web.options(API[i], handleOptions);
ts.web.get("/echo", handleEcho);
ts.web.post("/settings", handleSettings);
ts.web.post("/torrents", handleTorrents);
ts.web.post("/viewed", handleViewed);
ts.web.post("/cache", handleCache);
ts.web.get("/stream", handleStream);
ts.web.get("/stream/*path", handleStream);
ts.web.head("/stream", handleStream);
ts.web.head("/stream/*path", handleStream);
ts.web.get("/play/*path", handlePlay);
ts.web.head("/play/*path", handlePlay);

ts.web.staticFile("/", "index.html");
ts.web.staticFile("/index.html", "index.html");
ts.web.staticDir("/js", "js");
ts.web.staticDir("/img", "img");

console.log("[TS API] ready");
