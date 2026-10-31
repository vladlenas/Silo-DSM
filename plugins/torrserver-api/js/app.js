'use strict';

var BASE = '/plugins/torrserver-api/';

function t(key, def) {
    if (typeof i18n !== 'undefined' && i18n.t) {
        var v = i18n.t('plugin.tsapi.' + key, def);
        if (v && v !== 'plugin.tsapi.' + key) return v;
    }
    return def;
}

function $(id) { return document.getElementById(id); }

function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function toast(msg) {
    var el = $('toast');
    el.textContent = msg;
    el.classList.add('show');
    setTimeout(function () { el.classList.remove('show'); }, 2500);
}

function api(path, body) {
    var opts = body === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    };
    return fetch(BASE + path, opts).then(function (r) { return r.json(); });
}

function address() { return location.protocol + '//' + location.host; }

function render(d) {
    var s = d.settings || {};
    $('f-user').innerHTML = (d.users || []).map(function (u) {
        return '<option value="' + esc(u.id) + '"' + (u.id === s.user_id ? ' selected' : '') + '>' + esc(u.username) + '</option>';
    }).join('') || '<option value="owner">owner</option>';
    $('f-login').value = s.login || '';
    $('pass-state').textContent = s.has_password
        ? t('pass_set', 'A password is set: the app has to send it.')
        : t('pass_none', 'No password: anyone in your network can add torrents through this address.');
    $('f-token').placeholder = s.has_token ? t('saved', 'saved') : '';
    var list = d.recent || [];
    $('recent').innerHTML = list.length ? list.map(function (r) {
        var when = new Date(r.at).toLocaleString();
        return '<li><span class="' + (r.ok ? 'ok' : 'err') + '">' + (r.ok ? '✓' : '✗') + '</span> ' + esc(r.title || r.hash) +
            ' <span class="hint">· ' + esc(when) + (r.error ? ' · ' + esc(r.error) : '') + '</span></li>';
    }).join('') : '<li class="hint">' + esc(t('recent_none', 'Nothing yet.')) + '</li>';
}

function load() { return api('api/status').then(render).catch(function (e) { toast(e.message); }); }

$('addr').textContent = address();
$('btn-copy').addEventListener('click', function () {
    var s = address();
    var done = function () { toast(t('copied', 'Copied')); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(s).then(done);
    else {
        var ta = document.createElement('textarea'); ta.value = s; document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); done(); } catch (e) { /* nothing */ }
        ta.remove();
    }
});
$('btn-save').addEventListener('click', function () {
    api('api/settings', {
        user_id: $('f-user').value, login: $('f-login').value, password: $('f-pass').value, token: $('f-token').value
    }).then(function () { $('f-pass').value = ''; $('f-token').value = ''; toast(t('saved_msg', 'Saved')); return load(); });
});
$('btn-clear-pass').addEventListener('click', function () {
    api('api/settings', { user_id: $('f-user').value, login: '', clear_password: true })
        .then(function () { toast(t('saved_msg', 'Saved')); return load(); });
});

if (typeof i18n !== 'undefined' && i18n.apply) i18n.apply();
load();
