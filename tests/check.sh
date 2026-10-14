#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
rendered_properties=$(mktemp)
trap 'rm -f "${rendered_properties}"' EXIT

while IFS= read -r script; do
  bash -n "${script}"
done < <(find "${repo_root}/scripts" "${repo_root}/tests" -maxdepth 1 \
  -type f -name '*.sh' -print | sort)

python3 -m py_compile "${repo_root}/tests/bedrock_ping.py"
python3 -m py_compile "${repo_root}/monitor/monitor.py"
PUBLIC_IP=203.0.113.10 SERVER_NAME='Public Test Server' \
  "${repo_root}/scripts/render-server-properties.sh" \
  "${repo_root}/config/server.properties" "${rendered_properties}"
grep -qx 'server-name=Public Test Server' "${rendered_properties}"
[[ $(grep -c '^server-udp-ports=203\.0\.113\.10:32[0-9][0-9][0-9]:32[0-9][0-9][0-9]$' \
  "${rendered_properties}") -eq 16 ]]
PYTHONPATH="${repo_root}/monitor" python3 -m unittest discover \
  -s "${repo_root}/tests"

if command -v node >/dev/null 2>&1; then
  node --check "${repo_root}/monitor/web/app.js"
else
  echo "node is not installed; JavaScript syntax check skipped."
fi

if command -v shellcheck >/dev/null 2>&1; then
  find "${repo_root}/scripts" "${repo_root}/tests" -maxdepth 1 \
    -type f -name '*.sh' -print0 \
    | sort -z \
    | xargs -0 shellcheck
else
  echo "shellcheck is not installed; skipped."
fi

echo "Checks passed."
