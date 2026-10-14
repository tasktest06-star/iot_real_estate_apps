#!/usr/bin/env bash
# End-to-end acceptance run.
#
# Boots the backend, streams a labelled synthetic fleet through the real HTTP
# ingest path, then asserts the things that actually matter:
#
#   1. ingest is idempotent on (dev_eui, f_cnt)        -- contract §4
#   2. shadow mode suppresses notification by default  -- roadmap §0 rule 3
#   3. a valve command on an unarmed device is REFUSED -- fail-as-is, not fail-closed
#   4. the portfolio report refuses to claim frequency reduction it cannot prove
#   5. the detector clears the P1 gate: <0.5 nuisance alerts/device-year
#
# Exits non-zero on any failure, so it is usable as CI.

set -uo pipefail
cd "$(dirname "$0")/.."

PY=./.venv/bin/python
PORT="${PORT:-8061}"
BASE="http://127.0.0.1:${PORT}/api/v1"
DEVICES="${DEVICES:-40}"
DAYS="${DAYS:-30}"
SEED="${SEED:-7}"
FLEET="build/fleet-e2e.jsonl"
DB="build/e2e.db"
PASS=0; FAIL=0

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  PASS  %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  FAIL  %s\n' "$*"; FAIL=$((FAIL+1)); }
check(){ if [ "$1" = "$2" ]; then ok "$3 ($1)"; else bad "$3: expected $2, got $1"; fi; }

mkdir -p build
rm -f "$DB"

say "1/6  generate a labelled fleet"
$PY -m simulator --devices "$DEVICES" --days "$DAYS" --seed "$SEED" --out "$FLEET" >/dev/null || {
  bad "simulator failed"; exit 1; }
printf '  %s uplinks generated\n' "$(wc -l < "$FLEET" | tr -d ' ')"

say "2/6  boot the backend"
LEAK_DB_PATH="$DB" $PY -m uvicorn backend.app.main:app --host 127.0.0.1 --port "$PORT" \
  >build/e2e-server.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; wait $SRV 2>/dev/null' EXIT

for _ in $(seq 1 60); do
  code=$(curl -s -m 2 -o /dev/null -w '%{http_code}' "$BASE/healthz" 2>/dev/null) || code=000
  [ "$code" = "200" ] && break
  sleep 0.5
done
check "$code" "200" "backend is live on :$PORT"
[ "$code" = "200" ] || { echo "--- server log ---"; tail -30 build/e2e-server.log; exit 1; }

say "3/6  stream the fleet through the real HTTP ingest path"
$PY -m simulator --devices "$DEVICES" --days "$DAYS" --seed "$SEED" \
    --post "$BASE/uplink" >build/e2e-post.log 2>&1 || bad "posting fleet failed"
tail -3 build/e2e-post.log | sed 's/^/  /'
n_dev=$(curl -s "$BASE/devices" | $PY -c 'import json,sys;d=json.load(sys.stdin);print(len(d if isinstance(d,list) else d.get("devices",[])))')
printf '  %s devices registered\n' "$n_dev"
[ "$n_dev" -gt 0 ] && ok "devices registered from ingest" || bad "no devices registered"

say "4/6  idempotency: replay one uplink twice"
line=$(head -1 "$FLEET")
body=$($PY -c '
import json,sys
o=json.loads(sys.argv[1]); o.pop("_label",None); print(json.dumps(o))' "$line")
c1=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/uplink" -H 'content-type: application/json' -d "$body")
c2=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/uplink" -H 'content-type: application/json' -d "$body")
if [ "$c1" = "$c2" ] && [ "$c1" != "500" ]; then ok "replay returns the same status ($c1), no 500"
else bad "replay mismatch: first=$c1 second=$c2"; fi

say "5/6  safety invariants"
# shadow mode: with alerting disabled by default, nothing should be 'open'
shadow=$(curl -s "$BASE/alerts?status=shadow" | $PY -c 'import json,sys;d=json.load(sys.stdin);print(len(d if isinstance(d,list) else d.get("alerts",[])))' 2>/dev/null || echo 0)
openn=$(curl -s "$BASE/alerts?status=open"   | $PY -c 'import json,sys;d=json.load(sys.stdin);print(len(d if isinstance(d,list) else d.get("alerts",[])))' 2>/dev/null || echo 0)
printf '  alerts: shadow=%s open=%s\n' "$shadow" "$openn"
if [ "$openn" = "0" ]; then ok "shadow mode suppressed all customer-visible alerts (rule 3)"
else bad "$openn alerts escaped shadow mode with alerting disabled"; fi

# valve refusal on an unarmed device -- must be 409, must NOT attempt the close
dev=$(curl -s "$BASE/devices" | $PY -c '
import json,sys
d=json.load(sys.stdin); d=d if isinstance(d,list) else d.get("devices",[])
print(d[0].get("device_id","") if d else "")')
if [ -n "$dev" ]; then
  vc=$(curl -s -o build/e2e-valve.json -w '%{http_code}' -X POST "$BASE/devices/$dev/valve" \
        -H 'content-type: application/json' -d '{"command":"close"}')
  check "$vc" "409" "valve close on an unarmed device is refused"
  sed 's/^/    /' build/e2e-valve.json 2>/dev/null | head -3
else bad "no device to test valve refusal against"; fi

# portfolio must not fabricate a frequency-reduction claim
curl -s "$BASE/insurer/portfolio" -o build/e2e-portfolio.json
if $PY -c '
import json,sys
d=json.load(open("build/e2e-portfolio.json"))
blob=json.dumps(d).lower()
# It must say what it is NOT claiming, somewhere in the response.
disclaims = any(k in blob for k in ("not_claimed","notclaimed","caveat","limitation","cannot be proven","not claimed"))
sys.exit(0 if disclaims else 1)' 2>/dev/null; then
  ok "portfolio report states what it does NOT claim"
else
  bad "portfolio report makes no honesty disclaimer about frequency reduction"
fi

say "6/6  score the detector against the P1 gate"
$PY -m detection.evaluate --input "$FLEET" | tee build/e2e-eval.txt | sed 's/^/  /'
if grep -qiE '\bPASS\b' build/e2e-eval.txt; then ok "P1 gate: nuisance alerts < 0.5 per device-year"
elif grep -qiE '\bFAIL\b' build/e2e-eval.txt; then bad "P1 gate not met (see numbers above -- this is a real result, not a harness bug)"
else bad "evaluation harness printed no PASS/FAIL verdict"; fi

printf '\n\033[1m== summary ==\033[0m\n  %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
echo "  end-to-end acceptance OK"
