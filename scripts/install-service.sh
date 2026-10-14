#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test -x /usr/local/bin/box64
test -x /opt/minecraft/server/bedrock_server
id minecraft >/dev/null

install -m 0644 "${repo_root}/systemd/minecraft.service" \
  /etc/systemd/system/minecraft.service
install -m 0755 "${repo_root}/scripts/update-bedrock.sh" \
  /usr/local/sbin/update-minecraft-bedrock
install -m 0644 "${repo_root}/systemd/minecraft-update.service" \
  /etc/systemd/system/minecraft-update.service
install -m 0644 "${repo_root}/systemd/minecraft-update.timer" \
  /etc/systemd/system/minecraft-update.timer
systemctl daemon-reload
systemctl enable minecraft.service
systemctl enable --now minecraft-update.timer
systemctl cat --no-pager minecraft.service
systemctl cat --no-pager minecraft-update.timer

echo "Service installed but not started."
