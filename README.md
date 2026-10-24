# Silo DSM

Synology DSM 7 package for **TorrServer Silo**, the rewrite of TorrServer on
the [`silo` branch](https://github.com/YouROK/TorrServer/tree/silo).

> **Beta.** Silo is in early development and changes a lot. For daily use take
> [TorrServer DSM](https://github.com/vladlenas/TorrServer-DSM); both packages
> can run side by side.

| | TorrServer DSM | Silo DSM |
|---|---|---|
| Package / user | `TorrServer` | `TorrServerSilo` |
| Web port | 8090 | **8095** |
| Log | `TorrServer.log` | `Silo.log` |

## Installation

Requirements: DSM 7.3+, amd64 (Intel/AMD) model, the **SynoCommunity** package
source (`https://packages.synocommunity.com/`) for the **FFmpeg 8** dependency.

1. Package Center → Settings → Package Sources: add SynoCommunity and the
   SSpkS server that publishes Silo DSM (beta packages are hidden otherwise).
2. Install **TorrServer Silo**. The wizard asks for the port, an optional owner
   password and the transcoding mode.
3. Silo's web page: `http://<NAS>:8095`.

A `.spk` from [Releases](https://github.com/vladlenas/Silo-DSM/releases) can
also be installed manually (install FFmpeg 8 first).

## Helper

**TorrServer Silo** in the DSM main menu opens the Helper: status, logs and the
settings Silo only reads from `config.yaml` (port, data folder, log level,
owner password, transcoding). The package generates `config.yaml` itself; do
not edit it by hand.

Cache, peers and speed limits are set in Silo's own web settings.

**Data folder on a shared folder:** give the system user **TorrServerSilo**
Read/Write on that folder (Control Panel → Shared Folder → Permissions), then
choose it in the Helper. Existing data is not moved.

**VAAPI** (Intel): choose VAAPI in the Helper and press **Test VAAPI** on the
status page. A permission error is usually fixed by reinstalling the package.

## ViMu (WebDAV)

1. Silo → admin panel → **Plugins** → Store → install **WebDAV**.
2. On the plugin page set a **WebDAV password**.
3. In ViMu add a WebDAV source:
   - address: `<NAS>:8095/plugins/webdav/dav/`
   - user: your Silo user (`owner` without a Silo password)
   - password: the WebDAV password

## Import from TorrServer

**TorrServer Import** is built in (admin panel → Plugins, menu item in the
library). It copies the torrent list from TorrServer (e.g. TorrServer DSM on
8090) with title, poster and category.

1. Open **Import from TorrServer** in the library menu.
2. Enter the TorrServer address (`http://127.0.0.1:8090` on the same NAS) and
   its login if it has one; **Test connection**.
3. **Import** adds everything missing. **Scheduled import** (off until you
   turn it on) adds only new torrents; nothing is ever deleted.

Not needed? Disable it in the admin panel → Plugins.

## FUSE: the library as a folder

Silo can show its library as a read-only folder: categories, torrents and
their files, for Plex, Jellyfin, File Station or SMB. Files are streamed from
the torrent when read.

1. In a shared folder (e.g. `video`) create an empty folder, e.g. `Silo`, and
   give the system user **TorrServerSilo** Read/Write on the shared folder
   (Control Panel → Shared Folder → Edit → Permissions → System internal user).
2. Silo → admin panel → **Plugins** → Store → install **FUSE**. On its page
   enter `/volume1/video/Silo`, mount, and turn on mounting at start.
3. Other users and apps (Plex, SMB) only see the files with **allow other
   users** turned on in the plugin. That needs `user_allow_other` in
   `/etc/fuse.conf`, once, over SSH:
   `echo user_allow_other | sudo tee -a /etc/fuse.conf`
   (a DSM update may reset this file).

Helper → Status → **FUSE** shows whether `/dev/fuse`, `fusermount` and
`user_allow_other` are there and where Silo is mounted. A mount left behind by
a crash is released at the next start.

## Plex (optional plugin)

`plex-scan-<version>.zip` (attached to each release, not built in) asks Plex
to scan its libraries when torrents are added to, changed in or removed from
Silo, so a Plex library on the FUSE folder stays current without scheduled
scans.

1. Silo → admin panel → **Plugins** → upload the zip; **Plex** appears in the
   library menu.
2. Enter the Plex address (`http://127.0.0.1:32400` on the same NAS) and the
   Plex token (Plex Web → any movie → ⋯ → Get Info → View XML → the
   `X-Plex-Token` value in the address).
3. **Load libraries from Plex**, tick the libraries on the Silo folder, turn
   on **Scan automatically** and save.

Plex is asked at most once every 30 seconds; changes in between are sent
together.

## Transcoding profiles

The admin panel creates a profile with a few fields only. **Transcoding
profiles** in the library menu shows all of them: decoding on the video card
(needed for 4K and HDR with VAAPI), keeping video and audio that need no
transcoding, HDR to SDR, quality, bitrate, subtitles, HLS.

## Look

Silo in this package has two built-in themes:

- **DSM Style UI** (main page after installation): a light library in the
  style of DSM and of the package window, with categories, counters and
  search; the admin panel and the login page are light too.
- **Default Web UI**: Silo's own dark look, unchanged.

To switch: admin panel → Plugins → **Make theme** on the other one.

## Troubleshooting

- **Silo does not start / port in use:** Helper → Logs (`Silo.log`); change
  the port in the Helper.
- **"FFmpeg was not found":** install FFmpeg 8 from SynoCommunity.
- **The Helper window shows a Synology 404** (up to 0.9.1, after the package
  was stopped and started): DSM had not reloaded nginx. Fixed in later
  versions; once, reinstall the package over itself or run
  `sudo systemctl restart nginx`.

## Building

```sh
make        # spk/Silo-DSM-<version>-amd64.spk + spk/plex-scan-<version>.zip
make clean
```

Needs Go 1.26, git, xz and Python 3; `overlay/apply.py` adds DSM Style UI
and the plugins of `plugins/` to the Silo source as built-in plugins before
compiling, plus small fixes for Silo bugs (`overlay/core/`), and stops the
build when an upstream change no longer fits. `Makefile` pins `SILO_VERSION`, `SILO_COMMIT` and
`PKG_VERSION`; a change of `PKG_VERSION` on `main` builds and publishes a
GitHub pre-release with the `.spk`, the Plex plugin zip and `SHA256SUMS`.

Tests:

```sh
python3 -m unittest discover -s tests
sh tests/test_scripts.sh
bash tests/test_build.sh
node tests/test_ui.js
```

## Credits

Silo and TorrServer by [YouROK](https://github.com/YouROK). Package structure
and Helper from [TorrServer DSM](https://github.com/vladlenas/TorrServer-DSM).
