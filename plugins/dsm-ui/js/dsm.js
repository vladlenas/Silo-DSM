'use strict';

// DSM Style UI on top of the Default Web UI scripts.
//
// The scripts of the built-in Default Web UI (cards, add, info, cache,
// transcoding, menu) do the work; this file only adds what the DSM layout
// has on top: the category list with counters, the search field, a flat
// plugin list and the version under the name. It wraps the global functions
// of those scripts instead of copying them, so fixes in Silo arrive here too.
//
// Runs before app.js: init() of app.js starts on DOMContentLoaded and then
// already calls the wrapped functions.

(function () {
    var STORE_KEY = 'dsm-ui.category';

    function tr(key, fallback) {
        try {
            if (window.i18n && window.i18n.t) {
                var v = window.i18n.t(key);
                if (v && v !== key) return v;
            }
        } catch (e) { /* no dictionary */ }
        return fallback || key;
    }

    // ── Default Web UI missing ─────────────────────────────────────────────
    var needed = ['renderSync', 'upsertCard', 'removeCard', 'openInfoModal', 'bindMenu', 'loadPluginsMenu'];
    var missing = needed.some(function (name) { return typeof window[name] !== 'function'; });

    if (missing) {
        document.addEventListener('DOMContentLoaded', function () {
            document.querySelectorAll('#dsm-missing [data-i18n]').forEach(function (el) {
                el.textContent = tr(el.getAttribute('data-i18n'), el.textContent);
            });
            document.getElementById('app').hidden = true;
            document.getElementById('dsm-missing').hidden = false;
        });
        return;
    }

    // ── Categories ─────────────────────────────────────────────────────────
    // key null = all torrents; '' = without category. Keys are the ones the
    // Default Web UI writes (categories.js); anything else counts as "other".
    var CATEGORIES = [
        { key: null,    icon: 'ico-library', label: function () { return tr('dsm_all', 'All torrents'); } },
        { key: 'movie', icon: 'ico-movie',   label: function () { return tr('category_movie', 'Movies'); } },
        { key: 'tv',    icon: 'ico-tv',      label: function () { return tr('category_tv', 'Series'); } },
        { key: 'music', icon: 'ico-music',   label: function () { return tr('category_music', 'Music'); } },
        { key: 'other', icon: 'ico-other',   label: function () { return tr('category_other', 'Other'); } },
        { key: '',      icon: 'ico-none',    label: function () { return tr('category_none', 'No category'); } }
    ];
    var KNOWN = { movie: 1, tv: 1, music: 1, other: 1, '': 1 };

    var state = { category: null, query: '' };
    try {
        var saved = localStorage.getItem(STORE_KEY);
        if (saved !== null) {
            var parsed = JSON.parse(saved);
            if (parsed === null || KNOWN[parsed]) state.category = parsed;
        }
    } catch (e) { /* storage off */ }

    function categoryOf(card) {
        var c = (card && card.category) || '';
        return KNOWN[c] ? c : 'other';
    }

    function store() {
        // cards.js keeps the cards in a global const Map
        try { return cardsStore; } catch (e) { return new Map(); }
    }

    function matches(card) {
        if (!card) return true;
        if (state.category !== null && categoryOf(card) !== state.category) return false;
        if (state.query) {
            var hay = ((card.title || '') + ' ' + (card.hash || '')).toLowerCase();
            if (hay.indexOf(state.query) < 0) return false;
        }
        return true;
    }

    function renderCategories() {
        var box = document.getElementById('dsm-categories');
        if (!box) return;
        var counts = { all: 0 };
        store().forEach(function (card) {
            counts.all++;
            var k = categoryOf(card);
            counts[k] = (counts[k] || 0) + 1;
        });
        box.innerHTML = CATEGORIES.map(function (c) {
            var n = c.key === null ? counts.all : (counts[c.key] || 0);
            var active = c.key === state.category ? ' active' : '';
            return '<button class="nav-item' + active + '" type="button" data-category="' +
                (c.key === null ? '*' : c.key) + '">' +
                '<img class="nav-icon" src="img/' + c.icon + '.svg" alt="">' +
                '<span class="nav-label">' + esc(c.label()) + '</span>' +
                '<span class="nav-count">' + n + '</span>' +
                '</button>';
        }).join('');
    }

    function currentLabel() {
        for (var i = 0; i < CATEGORIES.length; i++) {
            if (CATEGORIES[i].key === state.category) return CATEGORIES[i].label();
        }
        return tr('library', 'Library');
    }

    function applyFilter() {
        var grid = document.getElementById('grid');
        if (!grid) return;
        var cards = store();
        var visible = 0;
        grid.querySelectorAll('.card').forEach(function (el) {
            var ok = matches(cards.get(el.getAttribute('data-hash')));
            el.hidden = !ok;
            if (ok) visible++;
        });
        document.getElementById('dsm-title').textContent = currentLabel();
        document.getElementById('dsm-count').textContent = cards.size ? String(visible) : '';
        document.getElementById('dsm-no-match').hidden = !(cards.size > 0 && visible === 0);
        renderCategories();
    }

    var pending = false;
    function scheduleFilter() {
        if (pending) return;
        pending = true;
        requestAnimationFrame(function () {
            pending = false;
            applyFilter();
        });
    }

    ['renderSync', 'upsertCard', 'removeCard'].forEach(function (name) {
        var original = window[name];
        window[name] = function () {
            var result = original.apply(this, arguments);
            scheduleFilter();
            return result;
        };
    });

    function selectCategory(key) {
        state.category = key;
        try { localStorage.setItem(STORE_KEY, JSON.stringify(key)); } catch (e) { /* storage off */ }
        applyFilter();
        if (typeof closeSidebar === 'function') closeSidebar();
    }

    // ── Plugin list: flat, admin panel first ───────────────────────────────
    window.loadPluginsMenu = async function () {
        var section = document.getElementById('plugins-section');
        var box = document.getElementById('plugins-menu');
        try {
            var res = await fetch('/api/ui/menu');
            if (!res.ok) { section.hidden = true; return; }
            var data = await res.json();
            var items = (data.menu || []).slice();
            if (!items.length) { section.hidden = true; return; }
            items.sort(function (a, b) {
                return (a.plugin_id === 'admin-ui' ? 0 : 1) - (b.plugin_id === 'admin-ui' ? 0 : 1);
            });
            box.innerHTML = items.map(function (it) {
                var label = it.title_key ? t(it.title_key) : (it.title || it.plugin_id);
                if (!label || label === it.title_key) label = it.title || it.plugin_name || it.plugin_id;
                var href = it.href || ('/plugins/' + it.plugin_id + '/');
                var icon = it.plugin_id === 'admin-ui' ? 'img/ico-settings.svg' : (it.icon || 'img/ico-plugin.svg');
                var own = it.plugin_id === 'admin-ui' || !it.icon;
                return '<a class="nav-item" href="' + esc(href) + '">' +
                    '<img class="nav-icon' + (own ? '' : ' plugin-icon') + '" src="' + esc(icon) +
                    '" alt="" onerror="this.src=\'img/ico-plugin.svg\';this.className=\'nav-icon\'">' +
                    '<span class="nav-label">' + esc(label) + '</span>' +
                    '</a>';
            }).join('');
            section.hidden = false;
        } catch (e) {
            section.hidden = true;
        }
    };

    // ── Start ──────────────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', function () {
        renderCategories();
        document.getElementById('dsm-title').textContent = currentLabel();

        document.getElementById('dsm-categories').addEventListener('click', function (e) {
            var btn = e.target.closest('[data-category]');
            if (!btn) return;
            var key = btn.getAttribute('data-category');
            selectCategory(key === '*' ? null : key);
        });

        var search = document.getElementById('dsm-search');
        search.setAttribute('placeholder', tr('dsm_search', 'Search'));
        search.addEventListener('input', function () {
            state.query = search.value.trim().toLowerCase();
            applyFilter();
        });

        // A click outside an open card menu closes it (DSM context menus do).
        document.addEventListener('click', function (e) {
            document.querySelectorAll('.card.menu-open').forEach(function (el) {
                if (!el.contains(e.target)) el.classList.remove('menu-open');
            });
        });

        fetch('/api/system/version').then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && d.version) document.getElementById('dsm-version').textContent = d.version;
            })
            .catch(function () { /* stays empty */ });
    });
})();
