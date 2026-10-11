// ===============================================================
// silo-webdav: read-only WebDAV share of the Silo torrent library
// ===============================================================
//
// Add this in the player (for example ViMu → Network → WebDAV):
//
//     http://<NAS>:<Silo port>/plugins/silo-webdav/
//
// The tree is ts.torrfs: categories (when there are two or more), torrents,
// their folders and files. Files are served by res.stream, which uses
// http.ServeContent in Silo: Range requests work, so the player can seek.
//
// Methods: OPTIONS, PROPFIND (Depth 0 and 1), GET, HEAD. Nothing can be
// written (WebDAV class 1 only, no LOCK).
//
// Login: when Silo has no owner password everyone is the owner and no login
// is asked. With a password, the player logs in with HTTP Basic using a Silo
// user name and password ("owner" and the owner password); the plugin checks
// them with Silo's own /api/auth/login.
//
// ES5 only: Silo runs plugins in goja.

var PREFIX = "/plugins/" + ts.info.plugin_id;
var AUTH_CACHE_MS = 10 * 60 * 1000;
var AUTH_FAIL_MS = 5 * 1000;
var authCache = {};

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

function xmlEscape(s) {
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

// "/plugins/silo-webdav/Movies/A b/" -> "Movies/A b"
function relPath(reqPath) {
    var p = String(reqPath || "");
    if (p.indexOf(PREFIX) === 0) {
        p = p.substring(PREFIX.length);
    }
    var parts = p.split("/");
    var clean = [];
    for (var i = 0; i < parts.length; i++) {
        var part = parts[i];
        if (part === "" || part === ".") continue;
        if (part === "..") {
            clean.pop();
            continue;
        }
        clean.push(part);
    }
    return clean.join("/");
}

// "Movies/A b" -> "/plugins/silo-webdav/Movies/A%20b/" (each segment encoded)
function hrefFor(path, isDir) {
    var out = PREFIX + "/";
    if (path) {
        var parts = path.split("/");
        var enc = [];
        for (var i = 0; i < parts.length; i++) {
            enc.push(encodeURIComponent(parts[i]));
        }
        out += enc.join("/");
        if (isDir) out += "/";
    }
    return out;
}

function httpDate(unixSeconds) {
    var ms = unixSeconds > 0 ? unixSeconds * 1000 : Date.now();
    return new Date(ms).toUTCString();
}

function rootNode() {
    return { name: "", path: "", is_dir: true, size: 0, mtime: 0, mime: "" };
}

function statPath(userId, path) {
    if (path === "") return rootNode();
    return ts.torrfs.stat(userId, path);
}

// ---------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------

function sendUnauthorized(res) {
    return res.status(401)
        .header("WWW-Authenticate", 'Basic realm="Silo", charset="UTF-8"')
        .text("authentication required");
}

// Port Silo listens on, from the Host header of this very request.
function siloPort(req) {
    var host = String(req.headers["host"] || "");
    var m = host.match(/:(\d+)$/);
    return m ? m[1] : "80";
}

function basicUser(req) {
    var header = String(req.headers["authorization"] || "");
    if (header.indexOf("Basic ") !== 0) return null;

    var key = ts.crypto.sha256(header);
    var now = Date.now();
    var cached = authCache[key];
    if (cached && cached.until > now) return cached.user;

    var user = null;
    try {
        var decoded = ts.crypto.base64decode(header.substring(6).trim());
        var colon = decoded.indexOf(":");
        if (colon > 0) {
            var resp = ts.http.post(
                "http://127.0.0.1:" + siloPort(req) + "/api/auth/login",
                { username: decoded.substring(0, colon), password: decoded.substring(colon + 1) },
                { headers: { "Content-Type": "application/json" } }
            );
            if (resp.ok) {
                var data = resp.json();
                if (data && data.user && data.user.id && !data.user.is_banned) {
                    user = { id: data.user.id, username: data.user.username };
                }
            }
        }
    } catch (e) {
        console.log("[WebDAV] login check failed: " + e);
        user = null;
    }

    authCache[key] = { user: user, until: now + (user ? AUTH_CACHE_MS : AUTH_FAIL_MS) };
    return user;
}

function currentUser(req) {
    if (req.user && req.user.id) return req.user;
    return basicUser(req);
}

// ---------------------------------------------------------------
// PROPFIND
// ---------------------------------------------------------------

function propResponse(node) {
    var isDir = !!node.is_dir;
    var name = node.name || ts.info.plugin_id;
    var props =
        "<D:displayname>" + xmlEscape(name) + "</D:displayname>" +
        "<D:getlastmodified>" + httpDate(node.mtime) + "</D:getlastmodified>";

    if (isDir) {
        props += "<D:resourcetype><D:collection/></D:resourcetype>";
    } else {
        props +=
            "<D:resourcetype/>" +
            "<D:getcontentlength>" + (node.size || 0) + "</D:getcontentlength>" +
            "<D:getcontenttype>" + xmlEscape(node.mime || "application/octet-stream") + "</D:getcontenttype>" +
            "<D:getetag>\"" + (node.size || 0) + "-" + (node.mtime || 0) + "\"</D:getetag>";
    }

    return "<D:response>" +
        "<D:href>" + xmlEscape(hrefFor(node.path, isDir)) + "</D:href>" +
        "<D:propstat><D:prop>" + props + "</D:prop>" +
        "<D:status>HTTP/1.1 200 OK</D:status></D:propstat>" +
        "</D:response>";
}

function handlePropfind(req, res) {
    var user = currentUser(req);
    if (!user) return sendUnauthorized(res);

    var path = relPath(req.path);
    var node = statPath(user.id, path);
    if (!node) return res.status(404).text("not found");

    var parts = [propResponse(node)];

    // Depth 0: only the resource itself. Depth 1 (and "infinity", which is
    // answered as 1): its children too.
    var depth = String(req.headers["depth"] || "1");
    if (node.is_dir && depth !== "0") {
        var children = ts.torrfs.list(user.id, path) || [];
        for (var i = 0; i < children.length; i++) {
            parts.push(propResponse(children[i]));
        }
    }

    var xml = '<?xml version="1.0" encoding="utf-8"?>' +
        '<D:multistatus xmlns:D="DAV:">' + parts.join("") + "</D:multistatus>";

    return res.status(207)
        .header("Content-Type", "application/xml; charset=utf-8")
        .text(xml);
}

// ---------------------------------------------------------------
// GET / HEAD
// ---------------------------------------------------------------

function directoryPage(user, path) {
    var children = ts.torrfs.list(user.id, path) || [];
    var rows = [];
    if (path !== "") {
        var parent = path.indexOf("/") >= 0 ? path.substring(0, path.lastIndexOf("/")) : "";
        rows.push('<li><a href="' + xmlEscape(hrefFor(parent, true)) + '">..</a></li>');
    }
    for (var i = 0; i < children.length; i++) {
        var c = children[i];
        rows.push('<li><a href="' + xmlEscape(hrefFor(c.path, c.is_dir)) + '">' +
            xmlEscape(c.name) + (c.is_dir ? "/" : "") + "</a></li>");
    }
    return '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Silo WebDAV</title></head>' +
        "<body><h3>/" + xmlEscape(path) + "</h3><ul>" + rows.join("") + "</ul></body></html>";
}

function handleGet(req, res) {
    var user = currentUser(req);
    if (!user) return sendUnauthorized(res);

    var path = relPath(req.path);
    var node = statPath(user.id, path);
    if (!node) return res.status(404).text("not found");

    // A folder opened in a browser: a plain list, handy for a quick check.
    if (node.is_dir) {
        if (req.method === "HEAD") {
            return res.status(200).header("Content-Type", "text/html; charset=utf-8").end();
        }
        return res.html(directoryPage(user, path));
    }

    var handle = ts.torrfs.open(user.id, path);
    if (!handle) return res.status(404).text("not found");

    // ServeContent in Silo answers Range, If-Range and HEAD itself.
    res.header("Content-Type", node.mime || "application/octet-stream");
    res.header("Accept-Ranges", "bytes");
    return res.stream(handle, node.name);
}

function handleOptions(req, res) {
    return res.status(200)
        .header("DAV", "1")
        .header("MS-Author-Via", "DAV")
        .header("Allow", "OPTIONS, PROPFIND, GET, HEAD")
        .end();
}

// ---------------------------------------------------------------
// Routes
// ---------------------------------------------------------------

ts.web.route("OPTIONS", "/*", handleOptions);
ts.web.route("PROPFIND", "/*", handlePropfind);
ts.web.route("GET", "/*", handleGet);
ts.web.route("HEAD", "/*", handleGet);

console.log("[WebDAV] Library shared at " + PREFIX + "/");
