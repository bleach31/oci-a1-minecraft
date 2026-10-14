#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

if [[ $(uname -m) != "aarch64" ]]; then
  echo "This installer requires an aarch64 host." >&2
  exit 1
fi

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "${repo_root}/config/versions.env"

apt-get update
apt-get install -y --no-install-recommends build-essential cmake git python3

source_dir="/usr/local/src/box64-${BOX64_VERSION}"
rm -rf "${source_dir}"
git clone --branch "${BOX64_VERSION}" --depth 1 \
  https://github.com/ptitSeb/box64.git "${source_dir}"

cmake -S "${source_dir}" -B "${source_dir}/build" \
  -DARM_DYNAREC=ON \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "${source_dir}/build" --parallel "$(nproc)"
cmake --install "${source_dir}/build"

box64 -v
install -d -m 0755 /opt/minecraft
{
  printf 'box64_version=%s\n' "${BOX64_VERSION}"
  printf 'box64_commit=%s\n' "$(git -C "${source_dir}" rev-parse HEAD)"
} > /opt/minecraft/box64-manifest.txt
