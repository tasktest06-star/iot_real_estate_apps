#!/usr/bin/env bash
# Re-verify every licence claim in data/repos.csv by reading each repository's
# actual LICENSE text from raw.githubusercontent.com.
#
# Exits non-zero if any repo's licence has drifted from what we recorded, so the
# tables in the Markdown and the slides stay reproducible by the reader.
#
# Uses raw.githubusercontent.com (unmetered) rather than the GitHub API, which is
# limited to 60 requests/hour unauthenticated and was already exhausted when this
# pack was built.
#
# WHY THIS CLASSIFIES BY LICENCE *TITLE* RATHER THAN BODY TEXT
# ------------------------------------------------------------
# The obvious implementation -- grep the body for "Apache License" and "Version
# 2.0" -- is wrong, and silently so. Two traps:
#
#   1. A Business Source License 1.1 file names Apache-2.0 as its *Change
#      License*, in the header. Body-matching therefore reports BUSL-licensed
#      ThingsBoard and EMQX as Apache-2.0 -- the exact opposite of the truth,
#      on the two packages most likely to end up in an IoT backend.
#   2. GPLv3 section 13 mentions the GNU Affero General Public License, so
#      body-matching reports every GPLv3 project as AGPL.
#
# Both produced false positives while this pack was being researched. So we take
# the first 12 non-empty lines (the licence's own title block) and classify on
# that, checking the source-available licences *before* the permissive ones.
#
# Usage: scripts/verify-licenses.sh [path/to/repos.csv]

set -uo pipefail

CSV="${1:-$(dirname "$0")/../data/repos.csv}"
PARALLEL="${PARALLEL:-12}"

if [[ ! -f "$CSV" ]]; then
  echo "error: cannot find $CSV" >&2
  exit 2
fi

# Python rather than a grep chain: ordering matters here and a chain of greps
# makes the precedence invisible. Reads stdin, prints one SPDX-ish token.
classify() {
  python3 -I -c '
import sys
t = sys.stdin.read(400000)
head = " | ".join(l.strip() for l in t.splitlines() if l.strip()).upper()[:1400]
body = t.upper()

# Source-available / copyleft FIRST: these name permissive licences inside
# themselves and would otherwise be misread (see header comment).
if "BUSINESS SOURCE LICENSE" in head:                     print("BUSL-1.1")
elif "SERVER SIDE PUBLIC LICENSE" in head:                 print("SSPL")
elif ("GPLV3" in head or "GPL-3" in head) and "MIT" in head: print("Split-GPLv3-MIT")
elif "GNU AFFERO" in head:                                 print("AGPL-3.0")
elif "GNU LESSER" in head:                                 print("LGPL")
elif "GNU GENERAL PUBLIC LICENSE" in head:                 print("GPL")
elif "EUROPEAN UNION PUBLIC LICENCE" in head or "EUPL" in head: print("EUPL-1.2")
elif "ECLIPSE PUBLIC LICENSE" in head:                     print("EPL")
elif "MOZILLA PUBLIC LICENSE" in head:                     print("MPL-2.0")
elif "COMMUNITY SPECIFICATION LICENSE" in head:            print("CommunitySpec-1.0")
elif "CLEAR BSD" in head:                                  print("BSD-3-Clear")
elif "APACHE LICENSE" in head:                             print("Apache-2.0")
elif "MIT LICENSE" in head or "PERMISSION IS HEREBY GRANTED, FREE OF CHARGE" in body[:4000]: print("MIT")
elif "REDISTRIBUTION AND USE IN SOURCE" in body:
    # Clear BSD can also announce itself only in body text.
    print("BSD-3-Clear" if "NO EXPRESS OR IMPLIED LICENSES TO ANY PARTY" in body else "BSD")
else:                                                      print("OTHER")
'
}

# Licence families we treat as equivalent to what is recorded in the CSV. The CSV
# records more specific strings than licence text alone can support -- a
# three-clause BSD file does not say "3-Clause", and a vendor licence with
# BSD-shaped redistribution terms is not BSD.
same_family() {
  local recorded="$1" found="$2"
  [[ "$recorded" == "$found" ]] && return 0
  case "$recorded:$found" in
    BSD-3-Clause:BSD|BSD-2-Clause:BSD|BSD*:BSD)      return 0 ;;
    GPL-3.0:GPL|GPL-2.0:GPL)                         return 0 ;;
    LGPL*:LGPL)                                      return 0 ;;
    EPL-2.0:EPL|EPL-1.0:EPL|EPL-2.0-or-EDL-1.0:EPL)  return 0 ;;
    # Vendor licences that borrow BSD or bespoke wording. The CSV is
    # authoritative and the role column explains each one.
    Nordic-5-Clause:BSD|Memfault-License:BSD)        return 0 ;;
    Microchip-proprietary:OTHER|Apache-2.0-plus-TSL:Apache-2.0) return 0 ;;
    MIT-root-EPL-files:MIT)                          return 0 ;;
  esac
  return 1
}

check_one() {
  local repo="$1" recorded="$2" body found
  for f in LICENSE LICENSE.txt LICENSE.md COPYING COPYING.txt LICENSE.TXT \
           LICENCE license.txt license.md LICENSE-APACHE \
           01-community_specification_license-v1.md; do
    body="$(curl -sfL --max-time 20 "https://raw.githubusercontent.com/${repo}/HEAD/${f}" 2>/dev/null)"
    if [[ -n "$body" ]]; then
      found="$(printf '%s' "$body" | classify)"
      if same_family "$recorded" "$found"; then
        printf 'ok    %-52s %s\n' "$repo" "$recorded"
      else
        printf 'DRIFT %-52s recorded=%s found=%s\n' "$repo" "$recorded" "$found"
      fi
      return
    fi
  done
  if [[ "$recorded" == "No-licence-file" ]]; then
    printf 'ok    %-52s %s\n' "$repo" "$recorded"
  else
    printf 'DRIFT %-52s recorded=%s found=NO_LICENSE_FILE\n' "$repo" "$recorded"
  fi
}
export -f check_one classify same_family

echo "Verifying licences from raw LICENSE text (parallel=$PARALLEL)..."
echo

results="$(
  tail -n +2 "$CSV" \
    | awk -F',' 'NF>2 && $1 !~ /^ *$/ {print $1 "\t" $2}' \
    | xargs -P "$PARALLEL" -d '\n' -I{} bash -c 'IFS=$'"'"'\t'"'"' read -r r l <<< "{}"; check_one "$r" "$l"' \
    | sort
)"

echo "$results"
echo

drift="$(grep -c '^DRIFT' <<<"$results" || true)"
total="$(grep -c . <<<"$results" || true)"

echo "----------------------------------------------------------"
printf 'checked %s repos, %s drifted\n' "$total" "$drift"

if [[ "$drift" -gt 0 ]]; then
  echo
  echo "A licence has changed since 2026-10-14, or a repo moved its LICENSE file."
  echo "Update data/repos.csv AND the tables in 05-open-source-stack.md and"
  echo "slides/main.tex. Do not just silence this -- ThingsBoard and EMQX both"
  echo "relicensed away from Apache-2.0 while remaining the top search result."
  exit 1
fi

echo "All licence claims reproduce."
