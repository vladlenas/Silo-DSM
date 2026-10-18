// DSM Style UI: static pages only. js/ holds the scripts of Silo's Default
// Web UI (copied at build time) plus js/dsm.js.
ts.web.staticFile("/", "index.html");
ts.web.staticFile("/index.html", "index.html");
ts.web.staticDir("/css", "css");
ts.web.staticDir("/js", "js");
ts.web.staticDir("/img", "img");
ts.web.staticDir("/i18n", "i18n");

console.log("[DSM UI] ready");
