// Transcoding Profiles: a page only. It reads and saves profiles through
// Silo's /api/transcode/profiles with the session of whoever opens it.
ts.web.staticFile("/", "index.html");
ts.web.staticFile("/index.html", "index.html");
ts.web.staticDir("/js", "js");
ts.web.staticDir("/img", "img");

console.log("[Profiles] ready");
