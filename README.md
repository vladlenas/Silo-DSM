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

`torrserver-import-<version>.zip` (attached to each release) is a Silo plugin
that copies the torrent list from TorrServer (e.g. TorrServer DSM on 8090) with
title, poster and category.

1. Silo → admin panel → **Plugins** → upload the zip and enable it.
2. Enter the TorrServer address (`http://127.0.0.1:8090` on the same NAS) and
   its login if it has one; **Test connection**.
3. **Import now** adds everything missing. **Automatic scan** (every 15 min by
   default) adds only new torrents; nothing is ever deleted.

## DSM look

Silo in this package looks like DSM and the package window: a light library
page with categories, counters and search, and the same style in the admin
panel and on the login page. Nothing to install: the look is compiled into
Silo (`overlay/`), and Silo's own library scripts are used unchanged.

## Troubleshooting

- **Silo does not start / port in use:** Helper → Logs (`Silo.log`); change
  the port in the Helper.
- **"FFmpeg was not found":** install FFmpeg 8 from SynoCommunity.
- **The Helper window shows a Synology 404:** DSM's nginx lost the package
  route (the Helper writes this to `Silo.log`). Reinstall the package over
  itself or run `sudo systemctl restart nginx`.

## Building

```sh
make        # spk/Silo-DSM-<version>-amd64.spk + spk/torrserver-import-<version>.zip
make clean
```

Needs Go 1.26, git, xz and Python 3; `overlay/apply.py` puts the DSM look
into the Silo source before compiling and stops the build when an upstream
change no longer fits. `Makefile` pins `SILO_VERSION`, `SILO_COMMIT` and
`PKG_VERSION`; a change of `PKG_VERSION` on `main` builds and publishes a
GitHub pre-release with the `.spk`, the plugin zip and `SHA256SUMS`.

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
