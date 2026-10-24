// TorrServer vs Silo: the test runs in the browser (it reads both streams the
// way a player does); the plugin only serves the page.
ts.web.staticFile("/", "index.html");
ts.web.staticFile("/index.html", "index.html");
ts.web.staticDir("/js", "js");
ts.web.staticDir("/img", "img");

console.log("[Battle] ready");
