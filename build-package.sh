#!/bin/bash
# Builds one .spk: Silo from a pinned commit of the TorrServer "silo" branch
# with the DSM look and the built-in plugins of overlay/ and plugins/ (see
# overlay/apply.py), compiled for one architecture, plus the package scripts,
# Helper and UI.
#
#   ./build-package.sh <silo version> <silo commit> <arch> <package version>
#
# Environment:
#   SILO_REPO          git URL of TorrServer (default: upstream on GitHub)
#   SILO_PREBUILT_DIR  take silo-linux-<arch> from this folder instead of
#                      compiling it (tests; machines without Go 1.26)
#   GO                 the go command (default: go). Go 1.26 or newer, or a
#                      Go that may download it (GOTOOLCHAIN=auto).
#   BUILD_ONLY=1       stop after compiling the binary
set -e

SILO_VERSION=$1
SILO_COMMIT=$2
ARCH=$3
PKG_VERSION=$4

SILO_REPO="${SILO_REPO:-https://github.com/YouROK/TorrServer.git}"
GO="${GO:-go}"

if [[ -z ${SILO_VERSION} || -z ${SILO_COMMIT} || -z ${ARCH} || -z ${PKG_VERSION} ]]; then
    echo "Usage: $0 <silo version> <silo commit> <arch> <package version>" >&2
    exit 1
fi

# A full commit id, so the pin cannot be moved like a branch or a tag.
if [[ ! ${SILO_COMMIT} =~ ^[0-9a-f]{40}$ ]]; then
    echo "ERROR: the Silo commit must be a full 40-character commit id: ${SILO_COMMIT}" >&2
    exit 1
fi

case "${ARCH}" in
    amd64) GOARCH=amd64; GOARM="" ;;
    arm64) GOARCH=arm64; GOARM="" ;;
    arm7)  GOARCH=arm;   GOARM=7 ;;
    *)
        echo "ERROR: Unsupported architecture: ${ARCH} (amd64, arm64, arm7)" >&2
        exit 1
        ;;
esac

SRC_DIR="dest_src/${SILO_COMMIT}"

# The web pages are compiled into the binary, so a cached binary is only
# reused for the same commit and the same overlay and plugins.
OVERLAY_ID="$(find overlay plugins -type f ! -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z |
    xargs -0 sha256sum | sha256sum | cut -c1-12)"
BIN_PATH="dest_bin/${SILO_COMMIT}-${OVERLAY_ID}/silo-linux-${ARCH}"

is_elf() {
    [[ "$(head -c 4 "$1" | od -An -c | tr -d ' ')" == '177ELF' ]]
}

# Fetches exactly the pinned commit (shallow) and checks that it is what was
# checked out. A finished checkout is kept for the other architectures.
fetch_source() {
    if [[ -f ${SRC_DIR}/.silo-dsm-ok ]] &&
       [[ "$(git -C "${SRC_DIR}" rev-parse HEAD 2>/dev/null)" == "${SILO_COMMIT}" ]]; then
        echo ">>> Source already present: ${SILO_COMMIT}"
        return
    fi

    echo ">>> Fetching Silo ${SILO_VERSION} (${SILO_COMMIT})"
    rm -rf "${SRC_DIR}"
    mkdir -p "${SRC_DIR}"
    git -C "${SRC_DIR}" init -q
    git -C "${SRC_DIR}" remote add origin "${SILO_REPO}"
    git -C "${SRC_DIR}" fetch -q --depth 1 origin "${SILO_COMMIT}"
    git -C "${SRC_DIR}" checkout -q --detach FETCH_HEAD

    local actual
    actual="$(git -C "${SRC_DIR}" rev-parse HEAD)"
    if [[ ${actual} != "${SILO_COMMIT}" ]]; then
        rm -rf "${SRC_DIR}"
        echo "ERROR: fetched ${actual}, expected ${SILO_COMMIT}" >&2
        exit 1
    fi

    touch "${SRC_DIR}/.silo-dsm-ok"
}

# Same flags as upstream build-all.sh (CGO off, nosqlite, -checklinkname=0),
# plus the version string.
compile_silo() {
    if [[ -s ${BIN_PATH} ]] && is_elf "${BIN_PATH}"; then
        echo ">>> Binary already built: silo-linux-${ARCH}"
        return
    fi

    mkdir -p "$(dirname "${BIN_PATH}")"

    if [[ -n ${SILO_PREBUILT_DIR} ]]; then
        echo ">>> Using prebuilt ${SILO_PREBUILT_DIR}/silo-linux-${ARCH}"
        cp "${SILO_PREBUILT_DIR}/silo-linux-${ARCH}" "${BIN_PATH}.part"
    else
        fetch_source
        python3 overlay/apply.py "${SRC_DIR}"
        echo ">>> Compiling silo-linux-${ARCH} with $(cd "${SRC_DIR}" && "${GO}" version)"
        local out
        out="$(pwd)/${BIN_PATH}.part"
        (
            cd "${SRC_DIR}"
            GOOS=linux GOARCH="${GOARCH}" GOARM="${GOARM}" CGO_ENABLED=0 \
                "${GO}" build \
                -trimpath -buildvcs=false -tags=nosqlite \
                -ldflags="-s -w -checklinkname=0 -X silo/internal/version.Version=${SILO_VERSION}" \
                -o "${out}" ./cmd/server
        )
    fi

    if ! is_elf "${BIN_PATH}.part"; then
        rm -f "${BIN_PATH}.part"
        echo "ERROR: silo-linux-${ARCH} is not an ELF binary" >&2
        exit 1
    fi

    mv -f "${BIN_PATH}.part" "${BIN_PATH}"
    echo ">>> sha256 $(sha256sum "${BIN_PATH}" | awk '{print $1}')  silo-linux-${ARCH}"
}

make_inner_pkg() {
    local tmp_dir=$1
    local dest_dir=$2
    local dest_pkg="$dest_dir/package.tgz"

    echo ">>> Making inner package.tgz"

    mkdir -p "${tmp_dir}/bin"
    cp -a "${BIN_PATH}" "${tmp_dir}/bin/silo"
    chmod 0755 "${tmp_dir}/bin/silo"

    cp -r src/ui "${tmp_dir}"
    cp -r src/nginx "${tmp_dir}"
    cp -a src/helper "${tmp_dir}/helper"
    rm -rf "${tmp_dir}/helper"/__pycache__
    chmod +x "${tmp_dir}/helper/helper.py" "${tmp_dir}/helper/siloconf.py"
    # Shown on the Helper status page (Silo itself has no --version).
    printf '%s %s\n' "${SILO_VERSION}" "${SILO_COMMIT:0:7}" > "${tmp_dir}/helper/VERSION"

    du -sk "${tmp_dir}" | awk '{print $1}' > "$dest_dir/extractsize_tmp"

    find "${tmp_dir}" -mindepth 1 -maxdepth 1 -printf '%f\n' | tar -cJf "${dest_pkg}" -C "${tmp_dir}" -T /dev/stdin
}

make_spk() {
    local spk_tmp_dir=$1
    local spk_dest_dir="./spk"
    local pkg_size
    pkg_size=$(cat "${spk_tmp_dir}/extractsize_tmp")
    local spk_filename="Silo-DSM-${PKG_VERSION}-${ARCH}.spk"

    echo ">>> Making spk: ${spk_filename}"

    mkdir -p "${spk_dest_dir}"
    rm -f "${spk_tmp_dir}/extractsize_tmp"

    cp -r src/scripts "${spk_tmp_dir}"
    chmod +x "${spk_tmp_dir}"/scripts/*
    cp src/PACKAGE_ICON_256.PNG src/PACKAGE_ICON.PNG "${spk_tmp_dir}"
    cp -r src/conf "${spk_tmp_dir}"
    cp -r src/WIZARD_UIFILES "${spk_tmp_dir}"

    ./src/INFO.sh "${PKG_VERSION}" "${ARCH}" "${pkg_size}" "${SILO_VERSION}" >"${spk_tmp_dir}/INFO"

    find "${spk_tmp_dir}" -mindepth 1 -maxdepth 1 -printf '%f\n' | tar -cf "${spk_dest_dir}/${spk_filename}" -C "${spk_tmp_dir}" -T /dev/stdin
}

make_pkg() {
    mkdir -p ./build

    local pkg_temp_dir spk_temp_dir
    pkg_temp_dir=$(mktemp -d -p ./build)
    spk_temp_dir=$(mktemp -d -p ./build)

    make_inner_pkg "${pkg_temp_dir}" "${spk_temp_dir}"
    make_spk "${spk_temp_dir}"

    rm -rf "${pkg_temp_dir}" "${spk_temp_dir}"
}

main() {
    echo ">>> Building package ${PKG_VERSION} (Silo ${SILO_VERSION}) for ${ARCH}"

    compile_silo

    if [[ ${BUILD_ONLY:-0} == 1 ]]; then
        echo ">>> Build only, skipping package"
        return
    fi

    make_pkg

    echo ">>> Done"
}

main
