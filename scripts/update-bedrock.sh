#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

install_root=/opt/minecraft
server_dir="${install_root}/server"
manifest="${install_root}/install-manifest.txt"
download_api=https://net-secondary.web.minecraft-services.net/api/v1.0/download/links
wiki_url=https://minecraft.wiki/w/Bedrock_Dedicated_Server
user_agent='Mozilla/5.0 (X11; Linux aarch64) Minecraft-BDS-Updater/1.0'

test -x "${server_dir}/bedrock_server"
test -f "${manifest}"
command -v box64 >/dev/null

requested_version=${1:-}
if [[ -n ${requested_version} ]]; then
  if [[ ! ${requested_version} =~ ^[0-9]+(\.[0-9]+)+$ ]]; then
    echo "Invalid BDS version: ${requested_version}" >&2
    exit 1
  fi
  download_url="https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-${requested_version}.zip"
  latest_version=${requested_version}
else
  download_url=""
  if api_response=$(curl --fail --location --retry 3 --user-agent "${user_agent}" "${download_api}"); then
    download_url=$(
      python3 -c 'import json, sys
links = json.load(sys.stdin)["result"]["links"]
print(next(item["downloadUrl"] for item in links
           if item["downloadType"] == "serverBedrockLinux"))' \
        <<<"${api_response}"
    ) || download_url=""
  fi
  if [[ -z ${download_url} ]]; then
    page=$(curl --fail --location --retry 3 --user-agent "${user_agent}" "${wiki_url}")
    download_url=$(
      grep -Eo 'https://www\.minecraft\.net/bedrockdedicatedserver/bin-linux/bedrock-server-[0-9.]+\.zip' \
        <<<"${page}" | sort -V | tail -n 1
    )
  fi
  if [[ -z ${download_url} ]]; then
    echo "Could not find an official BDS Linux download URL." >&2
    exit 1
  fi
  latest_version=${download_url##*/bedrock-server-}
  latest_version=${latest_version%.zip}
fi

case "${download_url}" in
  https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-*.zip) ;;
  *)
    echo "Refusing non-official download URL: ${download_url}" >&2
    exit 1
    ;;
esac

current_version=$(sed -n 's/^bds_version=//p' "${manifest}" | tail -n 1)
if [[ ${current_version} == "${latest_version}" ]]; then
  echo "BDS ${latest_version} is already installed."
  exit 0
fi

archive=$(mktemp --suffix=.zip)
staging=$(mktemp -d "${install_root}/server.next.XXXXXX")
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
rollback_dir="${install_root}/server.rollback.${timestamp}"
service_was_running=0
swapped=0

cleanup() {
  rm -f "${archive}"
  if [[ ${swapped} -eq 0 ]]; then
    rm -rf "${staging}"
  fi
}
trap cleanup EXIT

wget -nv --timeout=30 --tries=3 --user-agent="${user_agent}" \
  --output-document="${archive}" "${download_url}"
observed_sha256=$(sha256sum "${archive}" | awk '{print $1}')
unzip -q "${archive}" -d "${staging}"
test -x "${staging}/bedrock_server"

if systemctl is-active --quiet minecraft.service; then
  service_was_running=1
  systemctl stop minecraft.service
fi

for path in worlds server.properties allowlist.json permissions.json valid_known_packs.json; do
  if [[ -e "${server_dir}/${path}" ]]; then
    cp -a "${server_dir}/${path}" "${staging}/"
  fi
done

mv "${server_dir}" "${rollback_dir}"
mv "${staging}" "${server_dir}"
swapped=1
chown -R minecraft:minecraft "${server_dir}"

if [[ ${service_was_running} -eq 1 ]]; then
  if ! systemctl start minecraft.service; then
    systemctl stop minecraft.service || true
    failed_dir="${install_root}/server.failed.${timestamp}"
    mv "${server_dir}" "${failed_dir}"
    mv "${rollback_dir}" "${server_dir}"
    systemctl start minecraft.service
    echo "BDS ${latest_version} failed; restored ${current_version}." >&2
    exit 1
  fi
fi

{
  printf 'bds_version=%s\n' "${latest_version}"
  printf 'bds_url=%s\n' "${download_url}"
  printf 'bds_sha256=%s\n' "${observed_sha256}"
  printf 'installed_at=%s\n' "${timestamp}"
} > "${manifest}"

find "${install_root}" -maxdepth 1 -type d -name 'server.rollback.*' \
  -printf '%T@ %p\n' | sort -nr | tail -n +3 | cut -d' ' -f2- | xargs -r rm -rf

echo "Updated BDS from ${current_version:-unknown} to ${latest_version}."
echo "Rollback directory: ${rollback_dir}"
