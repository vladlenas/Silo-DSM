// Silo DSM: light DSM look for the admin panel, the login page and plugin
// pages while "DSM Style UI" is Silo's main page (theme). With Silo's own
// theme nothing changes. Loaded in <head>, before the page is drawn: the last
// known answer is applied at once, then checked against the server.
(function () {
    var KEY = 'silo-look';
    var root = document.documentElement;

    function apply(dsm) {
        root.classList.toggle('silo-dsm', dsm);
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
