#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run with sudo." >&2
    exit 1
fi

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
monitor_dir=/usr/local/lib/minecraft-monitor
environment_file=/etc/minecraft-monitor.env
monitor_user=${MONITOR_USER:-}
public_ip=${PUBLIC_IP:-}

if [[ -z ${MONITOR_PASSWORD:-} ]]; then
    echo "Set MONITOR_PASSWORD for HTTP Basic authentication." >&2
    exit 1
fi
if [[ -z ${monitor_user} ]]; then
    echo "Set MONITOR_USER for HTTP Basic authentication." >&2
    exit 1
fi
if [[ -z ${public_ip} ]]; then
    echo "Set PUBLIC_IP to the server's public IP address." >&2
    exit 1
fi
if [[ ! ${monitor_user} =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "MONITOR_USER contains unsupported characters." >&2
    exit 1
fi
if [[ ! ${public_ip} =~ ^[0-9a-fA-F:.]+$ ]]; then
    echo "PUBLIC_IP is not an IP address literal." >&2
    exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y apache2-utils nginx openssl python3

if ! id minecraft-monitor >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/minecraft-monitor \
        --shell /usr/sbin/nologin minecraft-monitor
fi

install -d -o root -g root -m 0755 "${monitor_dir}/web"
install -o root -g root -m 0644 "${repo_root}/monitor/monitor.py" "${monitor_dir}/monitor.py"
for asset in index.html app.js style.css; do
    install -o root -g root -m 0644 "${repo_root}/monitor/web/${asset}" "${monitor_dir}/web/${asset}"
done
install -o root -g root -m 0644 "${repo_root}/systemd/minecraft-monitor.service" \
    /etc/systemd/system/minecraft-monitor.service

domain_tls=false
if [[ -f /etc/nginx/sites-available/minecraft-monitor ]] && \
    grep -q '/etc/letsencrypt/live/' /etc/nginx/sites-available/minecraft-monitor; then
    domain_tls=true
else
    install -o root -g root -m 0644 "${repo_root}/config/nginx-minecraft-monitor.conf" \
        /etc/nginx/sites-available/minecraft-monitor
fi

if [[ ! -s ${environment_file} ]]; then
    ntfy_url=${NTFY_URL:-"https://ntfy.sh/minecraft-a1-$(openssl rand -hex 16)"}
    printf 'NTFY_URL=%s\n' "${ntfy_url}" > "${environment_file}"
    chmod 0600 "${environment_file}"
fi

printf '%s\n' "${MONITOR_PASSWORD}" | htpasswd -i -c -B \
    /etc/nginx/minecraft-monitor.htpasswd "${monitor_user}"
chmod 0640 /etc/nginx/minecraft-monitor.htpasswd
chown root:www-data /etc/nginx/minecraft-monitor.htpasswd

if [[ ${domain_tls} == false ]]; then
    openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 825 \
        -subj "/CN=${public_ip}" -addext "subjectAltName=IP:${public_ip}" \
        -keyout /etc/nginx/minecraft-monitor.key \
        -out /etc/nginx/minecraft-monitor.crt >/dev/null 2>&1
    chmod 0600 /etc/nginx/minecraft-monitor.key
    chmod 0644 /etc/nginx/minecraft-monitor.crt
fi

ln -sfn /etc/nginx/sites-available/minecraft-monitor \
    /etc/nginx/sites-enabled/minecraft-monitor
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl enable minecraft-monitor.service nginx.service
systemctl restart minecraft-monitor.service
systemctl restart nginx.service

echo "Monitor URL: https://${public_ip}/minecraft/"
echo "ntfy URL: $(sed -n 's/^NTFY_URL=//p' "${environment_file}")"