#!/bin/bash

set -e

PKG_VERSION="${1:?Package version is required}"
ARCH="${2:?Architecture is required}"
PKG_SIZE="${3:?Package size is required}"
SILO_VERSION="${4:?Silo version is required}"

TIMESTAMP="$(date -u +%Y%m%d-%H:%M:%S)"

# Same minimum as TorrServer DSM.
OS_MIN_VER="7.3-81180"

case "${ARCH}" in

    amd64)
        PLATFORMS="x86_64 apollolake avoton braswell broadwell broadwellnk broadwellnkv2 broadwellntbap bromolow denverton epyc7002 geminilake grantley kvmx64 purley r1000 v1000 v1000nk r1000nk geminilakenk icelaked epyc7003"
        ;;

    arm64)
        PLATFORMS="aarch64 armv8 rtd1296 rtd1619b armada37xx"
        ;;

    arm7)
        PLATFORMS="armv7 alpine alpine4k armada370 armada375 armada38x armadaxp monaco"
        ;;

    *)
        echo "ERROR: Unsupported architecture: ${ARCH}" >&2
        echo "Supported architectures: amd64, arm64, arm7" >&2
        exit 1
        ;;

esac

# beta="yes": Silo is an early rewrite. DSM 7 hides beta packages in the normal
# Package Center channel, so the package is distributed through SSpkS.
#
# install_dep_packages: FFmpeg 8 from SynoCommunity provides ffmpeg/ffprobe for
# transcoding (and pulls in synocli-videodriver for VAAPI on Intel). The
# SynoCommunity package source must be added in Package Center.
cat <<EOF
package="TorrServerSilo"
version="${PKG_VERSION}"
displayname="TorrServer Silo"
dsmappname="SYNO.SDS.TorrServerSilo.Application"
arch="${PLATFORMS}"
os_min_ver="${OS_MIN_VER}"
beta="yes"
install_dep_packages="ffmpeg8"
dsmuidir="ui"
startable="yes"
instuninst_restart_services="nginx.service"
maintainer="TorrServer"
maintainer_url="https://github.com/YouROK/TorrServer/tree/silo"
distributor="vladlenas"
distributor_url="https://github.com/vladlenas/Silo-DSM"
description="TorrServer Silo (${SILO_VERSION}), the from-scratch rewrite of TorrServer, packaged for DSM. Early development: expect things to break. Streams torrents over HTTP, with optional FFmpeg transcoding (VAAPI on Intel models). Runs next to TorrServer DSM on its own port and data folder."
description_rus="TorrServer Silo (${SILO_VERSION}), переписанный с нуля TorrServer, в пакете для DSM. Ранняя разработка: многое может не работать. Потоковая передача торрентов по HTTP, транскодирование через FFmpeg (VAAPI на моделях с Intel). Работает рядом с TorrServer DSM на своём порту и со своей папкой данных."
package_icon="PACKAGE_ICON.PNG"
package_icon_256="PACKAGE_ICON_256.PNG"
create_time="${TIMESTAMP}"
extractsize=${PKG_SIZE}
EOF
