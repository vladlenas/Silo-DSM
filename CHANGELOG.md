# Changelog

The top entry is used as the GitHub release notes; keep its version equal to
`PKG_VERSION` in `Makefile`.

## Unreleased

### Added

- **DSM Style UI**, a built-in Silo theme and the main page after
  installation: white side panel with categories (movies, series, music,
  other, no category) and counters, search, toolbar, DSM-like dialogs. The
  admin panel and the login page are light with it, with section icons and
  the package icon. Built on Silo's own library scripts.
- Silo's own **Default Web UI** stays built in and unchanged: admin panel →
  Plugins → "Make theme" brings the dark look back everywhere.
- **TorrServer Import is built in**: listed in the admin panel and in the
  library menu right after installation, no zip to upload. Scheduled import is
  off until it is turned on.

### Changed

- TorrServer Import 1.1.0: clearer Russian and English texts; connection
  errors are translated (wrong address, TorrServer not running, wrong login).

### Removed

- The plugin zip is no longer attached to releases (built in now). After the
  update, plugins `dsm-ui` and `torrserver-import` uploaded by hand are
  replaced by the built-in ones; their settings are kept.

## 0.7.1 (2026-10-17)

Silo stays at `Silo.1` (commit `fc5795e`).

### Added

- **TorrServer Import plugin** (`torrserver-import`, separate zip in the
  release): imports the torrent list of TorrServer (MatriX / TorrServer DSM)
  into Silo with title, poster and category — a button for a manual import
  and an automatic scan every N minutes (only torrents it has not seen
  before, so deleted ones stay deleted). Supports TorrServer's login.
- The Helper wakes the plugin's automatic scan once a minute (Silo plugins
  have no timers); without the plugin the call is ignored.
- The Helper notices when DSM's nginx loses the route to the Helper (the
  settings window then shows Synology's 404) and writes it to `Silo.log` with
  the fix: reinstall the package over itself or restart nginx.

### Changed

- **New package icon** in the style of Synology's own icons: the TorrServer DSM
  server with an orange "SILO" plate (upright bold type, like "SAN" or "VPN"),
  so the two packages read as one family and differ at a glance. Package
  Center and DSM window icons (16–256 px); the status page keeps Silo's logo.

## 0.6.1 (2026-10-11)

Silo stays at `Silo.1` (commit `fc5795e`).

### Changed

- **Status page:** in the Transcoding card the VAAPI test is now below the
  table, both at full width (the table rows no longer wrap).

## 0.5.1 (2026-10-11)

Silo stays at `Silo.1` (commit `fc5795e`).

### Changed

- **Status page is tighter.** Silo and System sit side by side with the same
  height, Transcoding spans the full width below them (table on the left,
  VAAPI test on the right), and the VAAPI result box is only as tall as its
  text. Before, the tall test result stretched the Silo card next to it.
- **Settings:** the "Torrents" card is gone; torrent settings are in Silo's web
  interface, as with TorrServer.

## 0.4.1 (2026-10-11)

Silo stays at `Silo.1` (commit `fc5795e`).

### Changed

- **FFmpeg 8.** The package now depends on FFmpeg 8 from SynoCommunity
  (`install_dep_packages="ffmpeg8"`, 8.1.3 with the Jellyfin patch set).
  DSM asks to install it before this update. Silo uses FFmpeg 8 when it is
  installed and falls back to FFmpeg 7, so nothing breaks while both are
  around; FFmpeg 7 can be uninstalled afterwards if nothing else needs it.
  The Helper shows the version in use and runs the VAAPI test with it.

## 0.3.1 (2026-10-11)

Silo stays at `Silo.1` (commit `fc5795e`).

### Fixed

- **Torrent settings in the Helper did nothing.** Cache size, peer limit and
  speed limits were written to the `torrent:` section of `config.yaml`, but
  Silo.1 does not read that section: its torrent engine keeps its settings in
  its own database and they are changed in Silo's web interface. The fields
  are gone from the Helper and from `config.yaml`; the Settings page now says
  where these settings are. Old values in `settings.json` are ignored.
- **Silo's working folder** is now the package folder
  (`/var/packages/TorrServerSilo/var`), so a relative disk-cache folder (Silo's
  default is `torrents`) is created there and not wherever DSM ran the script.

### Removed

- The package's own WebDAV plugin (`silo-webdav`). Silo's plugin store has a
  WebDAV plugin that works with ViMu; the README explains how to set it up
  (WebDAV password on the plugin page, address `/plugins/webdav/dav/`).
  Uninstall `silo-webdav` in Silo's admin panel if you installed it.

## 0.2.1 (2026-10-11)

Silo stays at `Silo.1` (commit `fc5795e`).

### Fixed

- **Helper showed Silo as stopped while it was running**, and a restart from
  the Helper stayed on "Restarting" until it gave up. DSM starts Silo by its
  real path (`/volume1/@appstore/TorrServerSilo/bin/silo`), while the Helper
  looked for `/var/packages/TorrServerSilo/target/bin/silo`, a link to the same
  file. Paths are now compared after resolving links.

## 0.1.1 (2026-10-11)

First beta. Silo `Silo.1` (commit `fc5795e`).

### Added

- DSM 7 package for TorrServer Silo, built like TorrServer DSM: `.spk` for
  `amd64`, `run-as: package`, Helper behind DSM nginx, release workflow.
- Silo compiled from a pinned commit of the TorrServer `silo` branch with Go
  1.26 (flags of upstream `build-all.sh`).
- `config.yaml` generated from the install wizard and the Helper (port, data
  folder, torrent limits, log level, owner password, transcoding), written
  again before every start.
- Own name, user, port (8095), Helper port (42778) and data folder, so the
  package runs next to TorrServer DSM.
- FFmpeg 7 from SynoCommunity as a dependency (`install_dep_packages`), first
  in Silo's `PATH`.
- GPU access for VAAPI (`video-driver` resource) and a **Test VAAPI** button
  with `/dev/dri` details on the Helper status page.
- `beta="yes"`: distributed through SSpkS; GitHub releases are pre-releases.
- **silo-webdav** plugin 0.1.0 (separate zip in the release): the torrent
  library as a read-only WebDAV share for players such as ViMu, with seeking
  and optional Basic login checked by Silo.
