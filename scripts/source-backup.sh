#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root on the source server." >&2
  exit 1
fi

if [[ ${CONFIRM_MINECRAFT_DOWNTIME:-} != "YES" ]]; then
  echo "Set CONFIRM_MINECRAFT_DOWNTIME=YES to stop Minecraft during backup." >&2
  exit 1
fi

source_dir=${1:-/opt/minecraft}
output_dir=${2:-"${PWD}/backups"}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="${output_dir}/minecraft-data-${timestamp}.tar.gz"
was_running=0

mkdir -p "${output_dir}"
if systemctl is-active --quiet minecraft.service; then
  systemctl stop minecraft.service
  was_running=1
fi

restart_source() {
  if [[ ${was_running} -eq 1 ]]; then
    systemctl start minecraft.service
  fi
}
trap restart_source EXIT

files=(worlds)
for candidate in server.properties allowlist.json permissions.json valid_known_packs.json; do
  if [[ -e "${source_dir}/${candidate}" ]]; then
    files+=("${candidate}")
  fi
done

test -d "${source_dir}/worlds"
tar -C "${source_dir}" -czf "${archive}" -- "${files[@]}"
(cd "${output_dir}" && sha256sum "$(basename "${archive}")") > "${archive}.sha256"
tar -tzf "${archive}" >/dev/null

echo "Created ${archive}"
echo "Created ${archive}.sha256"
