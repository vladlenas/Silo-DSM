#!/bin/sh
# Package scripts with a fake Silo binary and a fake Helper (no DSM needed).
# Run:  sh tests/test_scripts.sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0
TMP="$(mktemp -d)"

pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; FAILED=$((FAILED + 1)); }
check() { _d=$1; shift; if "$@"; then pass "$_d"; else fail "$_d"; fi; }

PY="$(command -v python3)"

export SYNOPKG_PKGNAME=TorrServerSilo
export SYNOPKG_DSM_VERSION_MAJOR=7
export SYNOPKG_PKGDEST="$TMP/target"
export SYNOPKG_PKGVAR="$TMP/var"
export SYNOPKG_TEMP_LOGFILE="$TMP/templog"
export PYTHON_CANDIDATES="$PY"
export FFMPEG_DIR="$TMP/ffmpeg7"
export SVC_WAIT_TIMEOUT=5

mkdir -p "$SYNOPKG_PKGDEST/bin" "$SYNOPKG_PKGDEST/helper" "$SYNOPKG_PKGDEST/ui" "$SYNOPKG_PKGVAR" "$FFMPEG_DIR"
cp "$ROOT/src/helper/siloconf.py" "$SYNOPKG_PKGDEST/helper/"
cp -r "$ROOT/src/scripts" "$TMP/scripts"
cp "$ROOT/src/ui/Silo.sc" "$SYNOPKG_PKGDEST/ui/"

# Fake Silo: records the PATH and config it got, then waits.
cat > "$SYNOPKG_PKGDEST/bin/silo" <<'EOF'
#!/bin/sh
echo "fake silo $* PATH=$PATH cwd=$(pwd)"
trap 'exit 0' TERM
while :; do sleep 1; done
EOF
chmod +x "$SYNOPKG_PKGDEST/bin/silo"
printf 'import time\nwhile True: time.sleep(1)\n' > "$SYNOPKG_PKGDEST/helper/helper.py"
printf '#!/bin/sh\n' > "$FFMPEG_DIR/ffmpeg"; chmod +x "$FFMPEG_DIR/ffmpeg"

cleanup() {
    for p in $(cat "$SYNOPKG_PKGVAR/TorrServerSilo.pid" 2>/dev/null); do kill -9 "$p" 2>/dev/null; done
    rm -rf "$TMP"
}
trap cleanup EXIT

SSS="$TMP/scripts/start-stop-status"
alive() { kill -0 "$1" 2>/dev/null && ! grep -q '^State:[[:space:]]*Z' "/proc/$1/status" 2>/dev/null; }

echo "== service-setup"
(
    . "$TMP/scripts/service-setup"
    check "ffmpeg7 comes first in PATH"     [ "${PATH%%:*}" = "$FFMPEG_DIR" ]
    check "silo runs with the config file"  [ "$SILO_COMMAND" = "$SYNOPKG_PKGDEST/bin/silo -c $SYNOPKG_PKGVAR/config.yaml" ]
    check "helper is the first command"     [ "$(printf '%s\n' "$SERVICE_COMMAND" | head -n 1)" = "$PY $SYNOPKG_PKGDEST/helper/helper.py" ]
    check "default port is 8095"            [ "$(silo_port)" = 8095 ]
    printf '{\n  "port": 8123\n}\n' > "$SYNOPKG_PKGVAR/settings.json"
    check "port is read from settings.json" [ "$(silo_port)" = 8123 ]
    rm -f "$SYNOPKG_PKGVAR/settings.json"
)

echo "== postinst takes the wizard answers"
(
    export wizard_port=8130 wizard_hwaccel_vaapi=true wizard_hwaccel_none=false
    sh "$TMP/scripts/postinst"
)
check "settings.json written"                grep -q '"port": 8130' "$SYNOPKG_PKGVAR/settings.json"
check "config.yaml written"                  grep -q 'hwaccel: "vaapi"' "$SYNOPKG_PKGVAR/config.yaml"
check "schema recorded"                      [ "$(cat "$SYNOPKG_PKGVAR/config.schema")" = 1 ]

echo "== start"
sh "$SSS" start
sleep 2
set -- $(cat "$SYNOPKG_PKGVAR/TorrServerSilo.pid" 2>/dev/null)
HELPER=$1; SILO=$2
check "two pids in the pid file"             [ -n "$HELPER" ] && [ -n "$SILO" ]
check "helper alive"                         alive "$HELPER"
check "silo alive"                           alive "$SILO"
check "status says running"                  sh "$SSS" status
check "silo got its config"                  grep -q "fake silo -c $SYNOPKG_PKGVAR/config.yaml" "$SYNOPKG_PKGVAR/Silo.log"
check "silo got ffmpeg7 first in PATH"       grep -q "PATH=$FFMPEG_DIR:" "$SYNOPKG_PKGVAR/Silo.log"
check "silo runs in the package var folder"  grep -q "cwd=$SYNOPKG_PKGVAR\$" "$SYNOPKG_PKGVAR/Silo.log"
check "firewall port follows settings"       grep -q 'dst.ports="8130/tcp"' "$SYNOPKG_PKGDEST/ui/Silo.sc"

echo "== restart (as the Helper does it)"
"$PY" - "$SYNOPKG_PKGVAR" <<'EOF'
import json, sys
p = sys.argv[1] + "/settings.json"; d = json.load(open(p)); d["port"] = 8131
json.dump(d, open(p, "w"))
EOF
SILO_HELPER_PID=$HELPER sh "$TMP/scripts/restart-silo"
sleep 1
set -- $(cat "$SYNOPKG_PKGVAR/TorrServerSilo.pid")
check "helper pid kept first"                [ "$1" = "$HELPER" ]
check "new silo pid"                         [ -n "$2" ] && [ "$2" != "$SILO" ]
check "old silo stopped"                     sh -c "! kill -0 $SILO 2>/dev/null || grep -q '^State:[[:space:]]*Z' /proc/$SILO/status"
check "config.yaml rewritten before start"   grep -q 'port: 8131' "$SYNOPKG_PKGVAR/config.yaml"
NEW_SILO=$2

echo "== stop"
sh "$SSS" stop
sleep 1
check "helper stopped"                       sh -c "! { kill -0 $HELPER 2>/dev/null && ! grep -q '^State:[[:space:]]*Z' /proc/$HELPER/status; }"
check "silo stopped"                         sh -c "! { kill -0 $NEW_SILO 2>/dev/null && ! grep -q '^State:[[:space:]]*Z' /proc/$NEW_SILO/status; }"
check "pid file removed"                     [ ! -e "$SYNOPKG_PKGVAR/TorrServerSilo.pid" ]
check "status says stopped"                  sh -c "! sh '$SSS' status >/dev/null"

echo "== missing ffmpeg7 is logged, not fatal"
rm -f "$FFMPEG_DIR/ffmpeg"
sh "$SSS" start; sleep 1
check "warning logged"                       grep -q 'FFmpeg 7 was not found' "$SYNOPKG_PKGVAR/Silo.log"
check "still running"                        sh "$SSS" status
sh "$SSS" stop

echo "== upgrade wizard reset"
(
    export wizard_reset_settings=true
    sh "$TMP/scripts/postupgrade"
)
check "settings back to defaults"            grep -q '"port": 8095' "$SYNOPKG_PKGVAR/settings.json"

echo
if [ "$FAILED" -eq 0 ]; then echo "ALL PASSED"; else echo "FAILED: $FAILED"; exit 1; fi
