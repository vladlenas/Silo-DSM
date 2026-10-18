// DSM Style UI: only static files. The library logic (cards, add, info,
// cache, transcoding) is loaded at runtime from the built-in Default Web UI
// (/plugins/default-ui/js/*), and js/dsm.js restyles what it draws.
ts.web.staticFile("/", "index.html");
ts.web.staticFile("/index.html", "index.html");
ts.web.staticDir("/css", "css");
ts.web.staticDir("/js", "js");
ts.web.staticDir("/img", "img");
ts.web.staticDir("/i18n", "i18n");

console.log("[DSM UI] ready");
