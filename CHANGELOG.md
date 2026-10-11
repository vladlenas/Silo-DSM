# Changelog

The top entry is used as the GitHub release notes; keep its version equal to
`PKG_VERSION` in `Makefile`.

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
