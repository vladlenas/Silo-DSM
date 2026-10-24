'use strict';

var BASE = '/plugins/plex-scan/';
var settings = null;
var sections = [];      // loaded from Plex: [{key, title, type, paths}]

function t(key, def) {
    if (typeof i18n !== 'undefined' && i18n.t) {
        var v = i18n.t(key, def);
        if (v && v !== key) return v;
    }
    return def || key;
}

function $(id) { return document.getElementById(id); }

function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function showToast(msg) {
    var el = $('toast');
    el.textContent = msg;
    el.classList.add('show');
    setTimeout(function () { el.classList.remove('show'); }, 2500);
}

function api(path, body) {
    var opts = body === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    };
    return fetch(BASE + 'api/' + path, opts).then(function (r) {
        return r.json().then(function (d) {
            if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
            return d;
        });
    });
}

// Error of the plugin: translated by its code, the English details after it.
function errorText(d) {
    if (!d || (!d.code && !d.error)) return '';
    var key = 'plugin.plex.err_' + d.code;
    var text = d.code ? t(key, '') : '';
    if (!text || text === key) return d.error || d.code;
    if (d.code === 'unreachable' || d.code === 'http' || d.code === 'scan_failed') {
        return text + (d.error ? ' (' + d.error + ')' : '');
    }
    return text;
}

function fmtTime(ms) { return ms ? new Date(ms).toLocaleString() : '–'; }

function reasonText(r) {
    return t('plugin.plex.reason_' + r, r);
}

function showState(st) {
    var line = st.last_scan
        ? t('plugin.plex.last_scan', 'Last scan:') + ' ' + fmtTime(st.last_scan) + ' (' + reasonText(st.reason) + ')' +
          (st.ok && st.scanned && st.scanned.length ? ' – ' + st.scanned.join(', ') : '')
        : t('plugin.plex.never', 'No scan yet.');
    if (st.pending) line += ' ' + t('plugin.plex.pending', 'Another scan is waiting.');
    $('last-scan').textContent = line;
    var msg = errorText(st);
    $('last-error').textContent = msg;
    $('last-error').className = 'hint ' + (msg ? 'msg-err' : '');
}

function chosen() {
    var out = [];
    document.querySelectorAll('#sections input[data-key]').forEach(function (el) {
        if (el.checked) out.push(el.getAttribute('data-key'));
    });
    return out;
}

function renderSections() {
    var want = settings ? settings.sections : [];
    var box = $('sections');
    if (!sections.length) {
        // Not loaded yet: show what is saved, so saving keeps it.
        box.innerHTML = want.map(function (k) {
            return '<label class="section"><input type="checkbox" data-key="' + esc(k) + '" checked>' +
                '<div>' + esc(t('plugin.plex.library', 'Library') + ' ' + k) + '</div></label>';
        }).join('');
        return;
    }
    box.innerHTML = sections.map(function (s) {
        return '<label class="section"><input type="checkbox" data-key="' + esc(s.key) + '"' +
            (want.indexOf(s.key) >= 0 ? ' checked' : '') + '>' +
            '<div><div>' + esc(s.title) + ' <span class="paths">(' + esc(s.type) + ')</span></div>' +
            '<div class="paths">' + esc(s.paths.join(', ')) + '</div></div></label>';
    }).join('');
}

function load() {
    return api('status').then(function (d) {
        settings = d.settings;
        $('f-url').value = settings.url || '';
        $('f-token').value = '';
        $('f-token').placeholder = settings.has_token ? '••••••••' : '';
        $('f-enabled').checked = !!settings.enabled;
        $('f-add').checked = !!settings.on_add;
        $('f-remove').checked = !!settings.on_remove;
        $('gap-hint').textContent = t('plugin.plex.gap_hint',
            'Plex is asked at most once every {n} s; changes in between are sent together.').replace('{n}', d.gap_sec);
        renderSections();
        showState(d.state);
    }).catch(function (e) { showToast(e.message); });
}

function save() {
    return api('settings', {
        enabled: $('f-enabled').checked,
        url: $('f-url').value,
        token: $('f-token').value,
        sections: chosen(),
        on_add: $('f-add').checked,
        on_remove: $('f-remove').checked
    }).then(function () {
        showToast(t('plugin.plex.saved', 'Settings saved'));
        return load();
    }).catch(function (e) {
        showToast(e.message === 'url_invalid' ? t('plugin.plex.url_invalid', 'Enter the Plex address') : e.message);
        throw e;
    });
}

function loadSections() {
    var msg = $('sections-msg');
    msg.textContent = t('plugin.plex.loading', 'Connecting…');
    msg.className = 'hint';
    save().then(function () { return api('sections', {}); }).then(function (d) {
        if (!d.ok) { msg.textContent = errorText(d); msg.className = 'hint msg-err'; return; }
        sections = d.sections || [];
        renderSections();
        msg.textContent = t('plugin.plex.sections_ok', 'Connected. Libraries in Plex:') + ' ' + sections.length;
        msg.className = 'hint msg-ok';
    }).catch(function (e) { msg.textContent = e.message; msg.className = 'hint msg-err'; });
}

function scanNow() {
    var btn = $('btn-scan');
    btn.disabled = true;
    api('scan', {}).then(function (st) {
        showState(st);
        if (st.ok) showToast(t('plugin.plex.scan_started', 'Plex is scanning'));
    }).catch(function (e) { showToast(e.message); })
      .then(function () { btn.disabled = false; });
}

document.addEventListener('DOMContentLoaded', function () {
    if (typeof i18n !== 'undefined' && i18n.apply) i18n.apply();
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
        el.textContent = t(el.getAttribute('data-i18n'), el.textContent);
    });
    $('btn-save').addEventListener('click', function () { save().catch(function () {}); });
    $('btn-sections').addEventListener('click', loadSections);
    $('btn-scan').addEventListener('click', scanNow);
    load();
});
