#!/bin/bash
# build-package.sh without network: a local git repository stands in for
# TorrServer, a fake "go" writes a fake binary.
# Run:  bash tests/test_build.sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; FAILED=$((FAILED + 1)); }
check() { local d=$1; shift; if "$@"; then pass "$d"; else fail "$d"; fi; }

# A tiny "TorrServer" with two commits.
UP="$TMP/upstream"
git init -q "$UP"
python3 "$ROOT/tests/silo_fixture.py" "$UP"
git -C "$UP" add -A
git -C "$UP" -c user.email=t@t -c user.name=t commit -q -m one
FIRST="$(git -C "$UP" rev-parse HEAD)"
git -C "$UP" -c user.email=t@t -c user.name=t commit -q --allow-empty -m two
git -C "$UP" config uploadpack.allowAnySHA1InWant true

# Fake go: checks it runs inside the checkout and records its environment.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/go" <<'EOF'
#!/bin/bash
[ "$1" = version ] && { echo "go version go1.26.0 fake"; exit 0; }
out=""; while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift;; esac; shift; done
look=no; [ -f internal/plugin/default_ui/js/dsm.js ] && grep -q 'Silo DSM: light DSM look' internal/web/static/css/style.css && look=yes
echo "$GOOS/$GOARCH/$GOARM/$CGO_ENABLED $(git rev-parse HEAD) look=$look" >> "$GO_LOG"
printf '\177ELFfake' > "$out"
EOF
chmod +x "$TMP/bin/go"
export PATH="$TMP/bin:$PATH" GO_LOG="$TMP/go.log" SILO_REPO="file://$UP"

fresh() {
    rm -rf "$TMP/w"; mkdir "$TMP/w"
    cp -r "$ROOT/build-package.sh" "$ROOT/src" "$ROOT/overlay" "$TMP/w/"
    cd "$TMP/w" || exit 1
}
build() { ./build-package.sh Silo.9 "$FIRST" "$1" 0.1.9 >"$TMP/out.log" 2>&1; }

echo "== builds the pinned commit, not the branch head"
fresh
check "amd64 builds"                       build amd64
check "go ran on the pinned commit"        grep -q "linux/amd64//0 $FIRST" "$GO_LOG"
check "DSM look applied before compiling"  grep -q "linux/amd64//0 $FIRST look=yes" "$GO_LOG"
check "spk created"                        [ -f spk/Silo-DSM-0.1.9-amd64.spk ]

echo "== arm7 is GOARCH=arm GOARM=7"
check "arm7 builds"                        build arm7
check "GOARM=7"                            grep -q "linux/arm/7/0 $FIRST" "$GO_LOG"
check "source fetched once"                grep -q "Source already present" "$TMP/out.log"

echo "== package contents"
mkdir x && tar -xf spk/Silo-DSM-0.1.9-amd64.spk -C x && mkdir x/pkg && tar -xJf x/package.tgz -C x/pkg
check "INFO: package name"                 grep -qx 'package="TorrServerSilo"' x/INFO
check "INFO: beta"                         grep -qx 'beta="yes"' x/INFO
check "INFO: depends on ffmpeg8"           grep -qx 'install_dep_packages="ffmpeg8"' x/INFO
check "INFO: version"                      grep -qx 'version="0.1.9"' x/INFO
check "INFO: Silo version in description"  grep -q 'description="TorrServer Silo (Silo.9)' x/INFO
check "binary is bin/silo, executable"     [ -x x/pkg/bin/silo ]
check "VERSION names the commit"           grep -qx "Silo.9 ${FIRST:0:7}" x/pkg/helper/VERSION
check "config generator shipped"           [ -x x/pkg/helper/siloconf.py ]
check "install wizard shipped"             [ -f x/WIZARD_UIFILES/install_uifile ]
check "scripts executable"                 [ -x x/scripts/start-stop-status ] && [ -x x/scripts/restart-silo ]
check "GPU access requested"               grep -q '"video-driver"' x/conf/resource
check "no build leftovers"                 sh -c '! tar -tJf x/package.tgz | grep -q __pycache__'

echo "== the overlay is part of the cache key"
check "binary cached per commit and overlay" sh -c "ls dest_bin/$FIRST-*/silo-linux-amd64 >/dev/null"
printf '\n/* changed */\n' >> overlay/static/dsm-core.css
check "changed overlay builds again"       build amd64
check "compiled again"                     [ "$(grep -c "linux/amd64//0 $FIRST" "$GO_LOG")" = 2 ]

echo "== refuses what it must"
fresh
check "short commit id refused"            sh -c '! ./build-package.sh Silo.9 abc123 amd64 0.1.9 >/dev/null 2>&1'
check "unknown arch refused"               sh -c "! ./build-package.sh Silo.9 $FIRST mips 0.1.9 >/dev/null 2>&1"
mkdir pre && printf 'not elf' > pre/silo-linux-amd64
check "non-ELF binary refused"             sh -c "! SILO_PREBUILT_DIR=pre ./build-package.sh Silo.9 $FIRST amd64 0.1.9 >/dev/null 2>&1"
check "nothing left in the cache"          sh -c "! ls dest_bin/$FIRST-*/silo-linux-amd64 >/dev/null 2>&1"
check "unknown commit refused"             sh -c "! ./build-package.sh Silo.9 $(printf 'f%.0s' $(seq 40)) amd64 0.1.9 >/dev/null 2>&1"

echo
if [ "$FAILED" -eq 0 ]; then echo "ALL PASSED"; else echo "FAILED: $FAILED"; exit 1; fi
