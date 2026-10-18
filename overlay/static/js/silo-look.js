// Silo DSM: light DSM look for the admin panel, the login page and plugin
// pages while "DSM Style UI" is Silo's main page (theme). With Silo's own
// theme nothing changes. Loaded in <head>, before the page is drawn: the last
// known answer is applied at once, then checked against the server.
(function () {
    var KEY = 'silo-look';
    var root = document.documentElement;

    // Tab icon: the package icon with DSM Style UI, Silo's own otherwise.
    var icons = null;
    function tabIcon(dsm) {
        if (!icons) {
            icons = Array.prototype.slice.call(document.querySelectorAll('link[rel="icon"]'));
            icons.forEach(function (l) { l.setAttribute('data-silo-href', l.getAttribute('href')); });
        }
        icons.forEach(function (l) {
            l.setAttribute('href', dsm ? '/img/dsm-logo.png' : l.getAttribute('data-silo-href'));
            if (dsm) l.removeAttribute('type'); else if (/\.svg$/.test(l.href)) l.setAttribute('type', 'image/svg+xml');
        });
    }

    function apply(dsm) {
        root.classList.toggle('silo-dsm', dsm);
        tabIcon(dsm);
    }

    function set(dsm) {
        apply(dsm);
        try { localStorage.setItem(KEY, dsm ? 'dsm' : 'silo'); } catch (e) { /* storage off */ }
    }

    var last = null;
    try { last = localStorage.getItem(KEY); } catch (e) { /* storage off */ }
    // DSM Style UI is the main page after installation.
    apply(last !== 'silo');

    // "/" redirects to the page of the current theme.
    fetch('/', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
        var path = new URL(r.url).pathname;
        if (path.indexOf('/plugins/dsm-ui/') === 0) set(true);
        else if (path.indexOf('/plugins/') === 0) set(false);
        // anything else (the login page): keep the last answer
    }).catch(function () { /* offline: keep the last answer */ });
})();
