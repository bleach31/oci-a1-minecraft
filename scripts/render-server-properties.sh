#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: PUBLIC_IP=ADDRESS $0 INPUT OUTPUT" >&2
    exit 1
fi

input=$1
output=$2
public_ip=${PUBLIC_IP:-}
server_name=${SERVER_NAME:-Bedrock Server}
udp_port_start=${UDP_PORT_START:-32000}
udp_port_count=${UDP_PORT_COUNT:-16}

python3 - "${public_ip}" "${udp_port_start}" "${udp_port_count}" <<'PY'
import ipaddress
import sys

address = ipaddress.ip_address(sys.argv[1])
if address.version != 4:
    raise SystemExit("PUBLIC_IP must be an IPv4 address")
start, count = map(int, sys.argv[2:])
if not 1024 <= start <= 65535 or not 1 <= count <= 100 or start + count - 1 > 65535:
    raise SystemExit("invalid UDP port allocation")
PY

if [[ ${server_name} == *';'* || ${server_name} == *$'\n'* ]]; then
    echo "SERVER_NAME contains unsupported characters." >&2
    exit 1
fi

awk -v name="${server_name}" -v ip="${public_ip}" \
    -v start="${udp_port_start}" -v count="${udp_port_count}" '
    /^server-name=/ { print "server-name=" name; next }
    /^server-udp-ports=/ { next }
    { print }
    END {
        for (offset = 0; offset < count; offset++) {
            port = start + offset
            print "server-udp-ports=" ip ":" port ":" port
        }
    }
' "${input}" > "${output}"