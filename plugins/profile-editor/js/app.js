'use strict';

// Every field of a Silo transcoding profile (internal/ffmpeg/profile and
// internal/ffmpeg/args of Silo). The page edits the profile as Silo returns
// it and sends it back whole, so fields not listed here stay as they are.

var API = '/api/transcode/profiles';

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

// [path, type, options or null, label key, English label]
var SECTIONS = [
    ['general', 'General', [
        ['name', 'text', null, 'name', 'Name'],
        ['protocol', 'select', ['hls', 'progressive'], 'protocol', 'Protocol'],
        ['container', 'select', ['ts', 'mp4', 'mkv'], 'container', 'Container'],
        ['visibility', 'select', ['private', 'public'], 'visibility', 'Visible to'],
    ]],
    ['video', 'Video', [
        ['video.codec', 'select', ['h264', 'hevc', 'av1', 'vp9', 'copy'], 'v_codec', 'Codec'],
        ['video.hwaccel', 'select', ['', 'none', 'vaapi'], 'v_hwaccel', 'Acceleration'],
        ['video.hw_decode', 'bool', null, 'v_hw_decode', 'Decode on the video card too'],
        ['allow_video_copy', 'bool', null, 'v_copy', 'Keep the video when it already has this codec'],
        ['video.max_height', 'int', null, 'v_max_height', 'Max height (0 = as source)'],
        ['video.max_width', 'int', null, 'v_max_width', 'Max width (0 = as source)'],
        ['video.max_framerate', 'float', null, 'v_max_fps', 'Max frame rate (0 = as source)'],
        ['video.crf', 'int', null, 'v_crf', 'Quality CRF / QP (0 = default 23, lower = better)'],
        ['video.bitrate_kbps', 'int', null, 'v_bitrate', 'Bitrate, kbit/s (0 = by quality)'],
        ['video.max_bitrate_kbps', 'int', null, 'v_max_bitrate', 'Max bitrate, kbit/s (0 = none)'],
        ['video.preset', 'select', ['', 'ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow'],
            'v_preset', 'Preset (CPU only)'],
        ['video.deinterlace', 'select', ['auto', 'off', 'yadif'], 'v_deinterlace', 'Deinterlace'],
        ['video.tonemap_mode', 'select', ['', 'on', 'off'], 'v_tonemap', 'HDR to SDR'],
        ['video.tonemap_algorithm', 'select', ['', 'hable', 'mobius', 'reinhard', 'clip'],
            'v_tonemap_alg', 'HDR to SDR method (CPU only)'],
    ]],
    ['audio', 'Audio', [
        ['audio.codec', 'select', ['aac', 'opus', 'mp3', 'ac3', 'flac', 'copy'], 'a_codec', 'Codec'],
        ['allow_audio_copy', 'bool', null, 'a_copy', 'Keep the audio when it already has this codec'],
        ['audio.bitrate_kbps', 'int', null, 'a_bitrate', 'Bitrate, kbit/s (0 = encoder default)'],
        ['audio.channels', 'int', null, 'a_channels', 'Channels (0 = as source, 2 = stereo)'],
        ['audio.sample_rate', 'int', null, 'a_rate', 'Sample rate (0 = as source)'],
    ]],
    ['subtitles', 'Subtitles', [
        ['subtitles.mode', 'select', ['off', 'copy', 'burn'], 's_mode', 'Subtitles'],
    ]],
    ['hls', 'HLS', [
        ['hls.segment_length', 'int', null, 'h_segment', 'Segment length, s'],
        ['hls.segment_type', 'select', ['mpegts', 'fmp4'], 'h_type', 'Segment type'],
        ['hls.playlist_type', 'select', ['', 'event', 'vod'], 'h_playlist', 'ffmpeg playlist'],
        ['hls.list_size', 'int', null, 'h_list_size', 'Segments ffmpeg keeps (0 = 6)'],
        ['hls.delete_threshold', 'int', null, 'h_delete', 'Extra segments before deleting (0 = 1)'],
    ]],
];

// Words for select values that are not self-explanatory.
var VALUE_LABELS = {
    'video.hwaccel': { '': ['val_from_package', 'as in the package settings'], none: ['val_cpu', 'CPU'],
        vaapi: ['val_vaapi', 'VAAPI (Intel)'] },
    'video.tonemap_mode': { '': ['val_auto_hdr', 'automatic (HDR sources only)'], on: ['val_always', 'always'],
        off: ['val_never', 'never'] },
    'video.tonemap_algorithm': { '': ['val_default_hable', 'default (hable)'] },
    'video.preset': { '': ['val_default', 'default'] },
    'video.deinterlace': { auto: ['val_auto', 'automatic'], off: ['val_off', 'off'] },
    'subtitles.mode': { off: ['val_off', 'off'], copy: ['val_sub_copy', 'copy tracks'],
        burn: ['val_sub_burn', 'burn into the picture (heavy)'] },
    visibility: { private: ['val_private', 'only me'], public: ['val_public', 'everyone'] },
    'hls.playlist_type': { '': ['val_sliding', 'sliding window (old segments deleted)'],
        event: ['val_event', 'keep every segment'], vod: ['val_vod', 'keep every segment (VOD)'] },
};

var HINTS = {
    video: ['hint_video', 'With VAAPI, turn on decoding on the video card for 4K and HDR: otherwise the processor unpacks the source.'],
    subtitles: ['hint_subs', 'Burning subtitles in is done by the processor and is slow on NAS models.'],
    hls: ['hint_hls', 'ffmpeg playlist: with the sliding window ffmpeg deletes old segments and keeps only the newest ones (the usual choice). "Keep every segment" deletes nothing while the film plays: no segment can go missing, but the transcoded film fills the cache folder (several GB). Segments ffmpeg keeps and extra segments only apply to the sliding window.'],
};

var profiles = [];
var current = null;

function get(obj, path) {
    var parts = path.split('.');
    for (var i = 0; i < parts.length; i++) {
        if (obj == null) return undefined;
        obj = obj[parts[i]];
    }
    return obj;
}

function set(obj, path, value) {
    var parts = path.split('.');
    for (var i = 0; i < parts.length - 1; i++) {
        if (obj[parts[i]] == null || typeof obj[parts[i]] !== 'object') obj[parts[i]] = {};
        obj = obj[parts[i]];
    }
    obj[parts[parts.length - 1]] = value;
}

function valueLabel(path, value) {
    var map = VALUE_LABELS[path];
    if (map && map[value]) return t('plugin.profed.' + map[value][0], map[value][1]);
    return value;
}

function fieldHTML(f, p) {
    var path = f[0], type = f[1], opts = f[2];
    var label = t('plugin.profed.' + f[3], f[4]);
    var value = get(p, path);
    var id = 'f-' + path.replace(/\./g, '-');

    if (type === 'bool') {
        return '<label class="check"><input type="checkbox" id="' + id + '" data-path="' + path +
            '" data-type="bool"' + (value ? ' checked' : '') + '> <span>' + esc(label) + '</span></label>';
    }
    var input;
    if (type === 'select') {
        var list = opts.slice();
        if (value != null && list.indexOf(value) < 0) list.push(value);   // keep unknown values
        input = '<select class="input" id="' + id + '" data-path="' + path + '" data-type="select">' +
            list.map(function (o) {
                return '<option value="' + esc(o) + '"' + ((value || '') === o ? ' selected' : '') + '>' +
                    esc(valueLabel(path, o)) + '</option>';
            }).join('') + '</select>';
    } else {
        var num = type === 'int' || type === 'float';
        input = '<input class="input" id="' + id + '" data-path="' + path + '" data-type="' + type + '"' +
            (num ? ' type="number" min="0"' + (type === 'float' ? ' step="any"' : '') : '') +
            ' value="' + esc(value == null ? (num ? 0 : '') : value) + '">';
    }
    return '<label class="field" for="' + id + '"><span>' + esc(label) + '</span>' + input + '</label>';
}

function render() {
    var form = $('form');
    if (!current) { form.innerHTML = ''; return; }
    form.innerHTML = SECTIONS.map(function (s) {
        var hint = HINTS[s[0]] ? '<p class="hint">' + esc(t('plugin.profed.' + HINTS[s[0]][0], HINTS[s[0]][1])) + '</p>' : '';
        var bools = s[2].filter(function (f) { return f[1] === 'bool'; });
        var others = s[2].filter(function (f) { return f[1] !== 'bool'; });
        return '<div class="panel panel-wide"><h2>' + esc(t('plugin.profed.sec_' + s[0], s[1])) + '</h2>' + hint +
            '<div class="grid2">' + others.map(function (f) { return fieldHTML(f, current); }).join('') + '</div>' +
            bools.map(function (f) { return fieldHTML(f, current); }).join('') + '</div>';
    }).join('') +
        '<div class="actions"><button class="btn" type="submit">' + esc(t('plugin.profed.save', 'Save')) + '</button></div>';
}

function readForm(base) {
    var p = JSON.parse(JSON.stringify(base));
    document.querySelectorAll('#form [data-path]').forEach(function (el) {
        var type = el.getAttribute('data-type');
        var v;
        if (type === 'bool') v = el.checked;
        else if (type === 'int') v = parseInt(el.value, 10) || 0;
        else if (type === 'float') v = parseFloat(el.value) || 0;
        else v = el.value;
        set(p, el.getAttribute('data-path'), v);
    });
    return p;
}

function toast(msg, err) {
    var el = $('toast');
    el.textContent = msg;
    el.className = 'show' + (err ? ' err' : '');
    setTimeout(function () { el.className = ''; }, 3000);
}

function request(method, url, body) {
    return fetch(url, {
        method: method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
            if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
            return d;
        });
    });
}

function fillPicker(selectId) {
    var pick = $('pick');
    pick.innerHTML = profiles.map(function (p) {
        return '<option value="' + esc(p.id) + '">' + esc(p.name) +
            (p.is_default ? ' ★' : '') + '</option>';
    }).join('');
    if (selectId) pick.value = selectId;
    current = profiles.filter(function (p) { return p.id === pick.value; })[0] || null;
    $('pick-hint').textContent = profiles.length ? '' :
        t('plugin.profed.none', 'No profiles yet: create one in the admin panel → Transcoding, then set it up here.');
    $('btn-copy').disabled = $('btn-default').disabled = !current;
    render();
}

function load(selectId) {
    return request('GET', API).then(function (d) {
        profiles = d.profiles || [];
        fillPicker(selectId || (current && current.id) ||
            (profiles.filter(function (p) { return p.is_default; })[0] || profiles[0] || {}).id);
    }).catch(function (e) { toast(e.message, true); });
}

document.addEventListener('DOMContentLoaded', function () {
    if (typeof i18n !== 'undefined' && i18n.apply) i18n.apply();
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
        el.textContent = t(el.getAttribute('data-i18n'), el.textContent);
    });

    $('pick').addEventListener('change', function () { fillPicker($('pick').value); });

    $('form').addEventListener('submit', function (e) {
        e.preventDefault();
        if (!current) return;
        var p = readForm(current);
        request('PUT', API + '/' + encodeURIComponent(current.id), p).then(function (saved) {
            toast(t('plugin.profed.saved', 'Saved'));
            return load(saved.id || current.id);
        }).catch(function (err) { toast(err.message, true); });
    });

    $('btn-copy').addEventListener('click', function () {
        if (!current) return;
        var p = readForm(current);
        delete p.id; delete p.is_default; delete p.created_at; delete p.updated_at; delete p.owner_id;
        p.name = (p.name || 'Profile') + ' ' + t('plugin.profed.copy_suffix', 'copy');
        request('POST', API, p).then(function (created) {
            toast(t('plugin.profed.created', 'Created'));
            return load(created.id);
        }).catch(function (err) { toast(err.message, true); });
    });

    $('btn-default').addEventListener('click', function () {
        if (!current) return;
        request('POST', API + '/' + encodeURIComponent(current.id) + '/default').then(function () {
            toast(t('plugin.profed.is_default', 'Now the default profile'));
            return load(current.id);
        }).catch(function (err) { toast(err.message, true); });
    });

    load();
});
