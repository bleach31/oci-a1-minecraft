#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

if [[ ${ACCEPT_MINECRAFT_EULA:-} != "TRUE" ]]; then
  echo "Set ACCEPT_MINECRAFT_EULA=TRUE after reading the Minecraft EULA." >&2
  exit 1
fi

if ! command -v box64 >/dev/null 2>&1; then
  echo "Box64 is not installed." >&2
  exit 1
fi

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "${repo_root}/config/versions.env"

if [[ -z ${PUBLIC_IP:-} ]]; then
  echo "Set PUBLIC_IP to the server's public IPv4 address." >&2
  exit 1
fi

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl unzip wget

install -d -m 0755 /opt/minecraft
if [[ -e /opt/minecraft/server ]]; then
  echo "/opt/minecraft/server already exists; refusing to overwrite it." >&2
  exit 1
fi

archive=$(mktemp --suffix=.zip)
staging=$(mktemp -d)
rendered_properties=$(mktemp)
trap 'rm -f "${archive}" "${rendered_properties}"; rm -rf "${staging}"' EXIT

properties_file=${SERVER_PROPERTIES_FILE:-${repo_root}/config/server.properties}
allowlist_file=${ALLOWLIST_FILE:-${repo_root}/config/allowlist.json}
permissions_file=${PERMISSIONS_FILE:-${repo_root}/config/permissions.json}
PUBLIC_IP=${PUBLIC_IP} SERVER_NAME=${SERVER_NAME:-Bedrock Server} \
  UDP_PORT_START=${UDP_PORT_START:-32000} UDP_PORT_COUNT=${UDP_PORT_COUNT:-16} \
  "${repo_root}/scripts/render-server-properties.sh" "${properties_file}" \
  "${rendered_properties}"
test -f "${allowlist_file}"
test -f "${permissions_file}"

url="https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-${BDS_VERSION}.zip"
archive_source=${url}
if [[ -n ${BDS_ARCHIVE:-} ]]; then
  source_archive=$(realpath "${BDS_ARCHIVE}")
  test -f "${source_archive}"
  cp -- "${source_archive}" "${archive}"
  archive_source=${source_archive}
else
  wget -nv --timeout=30 --tries=3 --output-document="${archive}" "${url}"
fi
observed_sha256=$(sha256sum "${archive}" | awk '{print $1}')
if [[ -n ${BDS_SHA256} && ${observed_sha256} != "${BDS_SHA256}" ]]; then
  echo "BDS archive checksum mismatch." >&2
  exit 1
fi

unzip -q "${archive}" -d "${staging}"
test -x "${staging}/bedrock_server"
mv "${staging}" /opt/minecraft/server
install -m 0644 "${rendered_properties}" /opt/minecraft/server/server.properties
install -m 0644 "${allowlist_file}" \
  /opt/minecraft/server/allowlist.json
install -m 0644 "${permissions_file}" \
  /opt/minecraft/server/permissions.json

if ! id minecraft >/dev/null 2>&1; then
  useradd --system --home-dir /opt/minecraft/server --shell /usr/sbin/nologin minecraft
fi
install -d -o minecraft -g minecraft -m 0750 /var/cache/minecraft-box64
chown -R minecraft:minecraft /opt/minecraft/server

{
  printf 'bds_version=%s\n' "${BDS_VERSION}"
  printf 'bds_url=%s\n' "${url}"
  printf 'bds_sha256=%s\n' "${observed_sha256}"
  printf 'archive_source=%s\n' "${archive_source}"
} > /opt/minecraft/install-manifest.txt

echo "Installed BDS ${BDS_VERSION}; service has not been started."
