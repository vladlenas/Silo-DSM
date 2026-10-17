'use strict';

var BASE = '/plugins/torrserver-import/';

function t(key, def) {
    return (typeof i18n !== 'undefined' && i18n.t) ? i18n.t(key, def) : (def || key);
}

function showToast(msg) {
    var el = document.getElementById('toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    setTimeout(function() { el.classList.remove('show'); }, 2500);
}

function $(id) { return document.getElementById(id); }

function api(path, body) {
    var opts = body === undefined ? {} : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    };
    return fetch(BASE + 'api/' + path, opts).then(function(r) {
        return r.json().then(function(d) {
            if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
            return d;
        });
    });
}

function fmtTime(ms) {
    return ms ? new Date(ms).toLocaleString() : '–';
}

function showState(st) {
    $('st-found').textContent = st.last_run ? st.found : '–';
    $('st-added').textContent = st.last_run ? st.added : '–';
    $('st-skipped').textContent = st.last_run ? st.skipped : '–';
    $('st-failed').textContent = st.last_run ? st.failed : '–';
    var mode = st.last_mode === 'auto' ? t('plugin.tsimport.mode_auto', 'automatic') : t('plugin.tsimport.mode_manual', 'manual');
    $('last-run').textContent = st.last_run
        ? t('plugin.tsimport.last_run', 'Last import:') + ' ' + fmtTime(st.last_run) + ' (' + mode + ')'
        : t('plugin.tsimport.never', 'No import yet.');
    var err = $('last-error');
    err.textContent = st.error || '';
    err.className = 'hint ' + (st.error ? 'msg-err' : '');
}

function load() {
    api('status').then(function(d) {
        var s = d.settings;
        $('f-url').value = s.url || '';
        $('f-user').value = s.username || '';
        $('f-pass').value = '';
        $('f-pass').placeholder = s.has_password ? '••••••••' : '';
        $('f-clear').checked = false;
        $('f-auto').checked = !!s.auto;
        $('f-interval').value = s.interval_min;
        $('next-run').textContent = s.auto
            ? t('plugin.tsimport.next_run', 'Next automatic scan:') + ' ' + fmtTime(d.next_run)
            : t('plugin.tsimport.auto_off', 'Automatic scan is off.');
        showState(d.state);
    }).catch(function(e) { showToast(e.message); });
}

function save() {
    return api('settings', {
        url: $('f-url').value,
        username: $('f-user').value,
        password: $('f-pass').value,
        clear_password: $('f-clear').checked,
        auto: $('f-auto').checked,
        interval_min: parseInt($('f-interval').value, 10)
    }).then(function() {
        showToast(t('plugin.tsimport.saved', 'Saved'));
        load();
    }).catch(function(e) {
        var key = e.message === 'url_invalid' ? 'plugin.tsimport.url_invalid'
            : e.message === 'interval_invalid' ? 'plugin.tsimport.interval_invalid' : '';
        showToast(key ? t(key) : e.message);
        throw e;
    });
}

function test() {
    var out = $('test-result');
    out.textContent = t('plugin.tsimport.testing', 'Connecting...');
    out.className = 'hint';
    save().then(function() { return api('test', {}); }).then(function(d) {
        out.textContent = d.ok
            ? t('plugin.tsimport.test_ok', 'Connected. Torrents in TorrServer:') + ' ' + d.count
            : d.error;
        out.className = 'hint ' + (d.ok ? 'msg-ok' : 'msg-err');
    }).catch(function(e) { out.textContent = e.message; out.className = 'hint msg-err'; });
}

function importNow() {
    var btn = $('btn-import');
    btn.disabled = true;
    api('import', {}).then(function(st) {
        showState(st);
        showToast(t('plugin.tsimport.added', 'Added') + ': ' + st.added);
    }).catch(function(e) { showToast(e.message); })
      .then(function() { btn.disabled = false; });
}

document.addEventListener('DOMContentLoaded', function() {
    $('btn-save').addEventListener('click', function() { save().catch(function() {}); });
    $('btn-test').addEventListener('click', test);
    $('btn-import').addEventListener('click', importNow);
    if (typeof i18n !== 'undefined' && i18n.apply) i18n.apply();
    load();
});
