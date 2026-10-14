#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root on the destination server." >&2
  exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 PATH_TO_BACKUP.tar.gz" >&2
  exit 1
fi

archive=$(realpath "$1")
checksum_file="${archive}.sha256"
destination=/opt/minecraft/server
rollback_root=/opt/minecraft/rollback
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive_listing=$(mktemp)
trap 'rm -f "${archive_listing}"' EXIT

test -f "${archive}"
if [[ -f ${checksum_file} ]]; then
  (cd "$(dirname "${archive}")" && sha256sum --check "$(basename "${checksum_file}")")
else
  echo "Checksum file not found: ${checksum_file}" >&2
  exit 1
fi

tar -tzf "${archive}" > "${archive_listing}"
if grep -Eq '(^/|(^|/)\.\.(/|$))' "${archive_listing}"; then
  echo "Archive contains an unsafe path." >&2
  exit 1
fi
grep -q '^worlds/' "${archive_listing}"

systemctl stop minecraft.service
install -d -m 0750 "${rollback_root}/${timestamp}"
for path in worlds server.properties allowlist.json permissions.json valid_known_packs.json; do
  if [[ -e "${destination}/${path}" ]]; then
    mv "${destination}/${path}" "${rollback_root}/${timestamp}/"
  fi
done

tar -C "${destination}" -xzf "${archive}"
chown -R minecraft:minecraft "${destination}"
systemctl start minecraft.service
systemctl --no-pager --full status minecraft.service

echo "Previous data is in ${rollback_root}/${timestamp}"
