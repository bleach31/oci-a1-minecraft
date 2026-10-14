#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run with sudo." >&2
    exit 1
fi

domain=${1:-}
if [[ ! ${domain} =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || [[ ${domain} != *.* ]]; then
    echo "Usage: sudo $0 <domain>" >&2
    exit 1
fi

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
challenge_root=/var/www/certbot
temporary_config=$(mktemp)
trap 'rm -f "${temporary_config}"' EXIT

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y certbot
install -d -o root -g root -m 0755 "${challenge_root}/.well-known/acme-challenge"

sed "s/__DOMAIN__/${domain}/g" \
    "${repo_root}/config/nginx-minecraft-monitor-acme.conf" > "${temporary_config}"
install -o root -g root -m 0644 "${temporary_config}" \
    /etc/nginx/sites-available/minecraft-monitor-acme
ln -sfn /etc/nginx/sites-available/minecraft-monitor-acme \
    /etc/nginx/sites-enabled/minecraft-monitor-acme
nginx -t
systemctl reload nginx.service

certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --webroot --webroot-path "${challenge_root}" --domain "${domain}"

sed "s/__DOMAIN__/${domain}/g" \
    "${repo_root}/config/nginx-minecraft-monitor-domain.conf" > "${temporary_config}"
install -o root -g root -m 0644 "${temporary_config}" \
    /etc/nginx/sites-available/minecraft-monitor
rm -f /etc/nginx/sites-enabled/minecraft-monitor-acme \
    /etc/nginx/sites-available/minecraft-monitor-acme

install -d -o root -g root -m 0755 /etc/letsencrypt/renewal-hooks/deploy
install -o root -g root -m 0755 "${repo_root}/scripts/certbot-reload-nginx.sh" \
    /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
nginx -t
systemctl reload nginx.service

echo "Monitor URL: https://${domain}/minecraft/"