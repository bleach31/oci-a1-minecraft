#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

systemctl is-active --quiet minecraft.service
systemctl --no-pager --full status minecraft.service | sed -n '1,20p'

for attempt in {1..60}; do
	if ss -ltnH | awk '$4 ~ /:19132$/ { found=1 } END { exit !found }'; then
		echo "BDS NetherNet listener is active on TCP port 19132."
		exit 0
	fi
	if [[ ${attempt} -eq 60 ]]; then
		echo "BDS is not listening on TCP port 19132 after 60 seconds." >&2
		exit 1
	fi
	sleep 1
done
