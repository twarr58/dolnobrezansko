#!/usr/bin/env bash
# Záložní proxy v ČR pro sběr úředních desek (HARVEST_FALLBACK_PROXY).
#
# Pro čerstvý Debian 12 nebo 13 na VPS s českou IP. Spustit jako root:
#   bash setup-squid-debian12.sh
# Volitelně: PORT=3128 SSH_PORT=22 EXTRA_DOMAINS=".obec.cz .jina-obec.cz" bash setup-squid-debian12.sh
#
# Co nastaví:
#   - squid s přihlášením jménem a heslem (heslo se vygeneruje, nebo se použije PROXY_PASSWORD)
#   - jen weby obcí mikroregionu (žádná otevřená proxy), jen porty 80/443, bez mezipaměti
#   - firewall nftables: dovnitř jen SSH a port proxy
#   - automatické bezpečnostní aktualizace
# Skript jde spustit znovu (např. po přidání domény); heslo zůstane, pokud nezadáš nové.
set -euo pipefail

PORT="${PORT:-3128}"
SSH_PORT="${SSH_PORT:-22}"
PROXY_USER="${PROXY_USER:-harvest}"
# Weby obcí Dolnobřežanska (tečka na začátku = i subdomény, např. www.)
DOMAINS=".vestec.cz .obecvestec.cz .mujesenice.cz .okrouhlo.cz .zlatniky-hodkovice.cz .brezova-olesko.cz
.ohrobec.cz .liber.cz .zvole.info .psary.cz .dolnibrezany.cz .vranenadvltavou.cz .imunis.cz .alis.cz ${EXTRA_DOMAINS:-}"

if [[ $EUID -ne 0 ]]; then echo "Spusť jako root (sudo bash $0)." >&2; exit 1; fi
if ! grep -qE 'VERSION_ID="(12|13)"' /etc/os-release; then echo "Upozornění: skript je psaný pro Debian 12 a 13." >&2; fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq squid apache2-utils nftables unattended-upgrades curl openssl >/dev/null

# --- heslo ---
if [[ -n "${PROXY_PASSWORD:-}" ]]; then
  PASS="$PROXY_PASSWORD"
elif [[ -f /etc/squid/harvest.pass ]]; then
  PASS="$(cat /etc/squid/harvest.pass)"
else
  PASS="$(openssl rand -hex 16)"
fi
umask 077
printf '%s' "$PASS" > /etc/squid/harvest.pass
htpasswd -b -c -m /etc/squid/passwords "$PROXY_USER" "$PASS" >/dev/null 2>&1
chown proxy:proxy /etc/squid/passwords /etc/squid/harvest.pass
chmod 640 /etc/squid/passwords
chmod 600 /etc/squid/harvest.pass
umask 022

# --- squid ---
cat > /etc/squid/squid.conf <<EOF
# Spravuje setup-squid-debian12.sh (sběr úředních desek). Ruční změny se při dalším spuštění přepíšou.
http_port ${PORT}

auth_param basic program /usr/lib/squid/basic_ncsa_auth /etc/squid/passwords
auth_param basic realm uredni-desky
auth_param basic credentialsttl 2 hours
acl authenticated proxy_auth REQUIRED

acl obce dstdomain $(echo $DOMAINS)
acl SSL_ports port 443
acl Safe_ports port 80 443
acl CONNECT method CONNECT

http_access deny !Safe_ports
http_access deny CONNECT !SSL_ports
http_access deny !obce
http_access allow authenticated
http_access deny all

# nic neukládat, neprozrazovat klienta
cache deny all
via off
forwarded_for delete
httpd_suppress_version_string on

access_log daemon:/var/log/squid/access.log squid
coredump_dir /var/spool/squid
EOF
squid -k parse >/dev/null 2>&1 || { squid -k parse; exit 1; }
systemctl enable --now squid >/dev/null
systemctl restart squid

# --- firewall ---
cat > /etc/nftables.conf <<EOF
#!/usr/sbin/nft -f
# Spravuje setup-squid-debian12.sh
flush ruleset
table inet filter {
  chain input {
    type filter hook input priority 0; policy drop;
    ct state established,related accept
    iif lo accept
    ip protocol icmp accept
    ip6 nexthdr ipv6-icmp accept
    tcp dport ${SSH_PORT} accept
    tcp dport ${PORT} accept
  }
  chain forward { type filter hook forward priority 0; policy drop; }
  chain output { type filter hook output priority 0; policy accept; }
}
EOF
nft -c -f /etc/nftables.conf
systemctl enable --now nftables >/dev/null
nft -f /etc/nftables.conf

# --- automatické bezpečnostní aktualizace ---
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades >/dev/null

# --- kontrola ---
IP="$(curl -s4 --max-time 10 https://ifconfig.me || hostname -I | awk '{print $1}')"
echo
echo "Proxy běží na portu ${PORT}. Zkouška přes ni (má vrátit HTTP 200):"
for url in https://www.okrouhlo.cz/uredni-deska/2 https://www.mujesenice.cz/uredni-deska/2 https://vestec.cz/obec-vestec/urad/uredni-deska/; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -x "http://${PROXY_USER}:${PASS}@127.0.0.1:${PORT}" \
    -A 'uredni-desky-harvester/0.1 (+https://github.com/twarr58/dolnobrezansko)' "$url" || true)"
  echo "  ${code}  ${url}"
done
echo
echo "Do GitHubu: Settings → Secrets and variables → Actions → Secrets → New repository secret"
echo "  Name:  HARVEST_FALLBACK_PROXY"
echo "  Value: http://${PROXY_USER}:${PASS}@${IP}:${PORT}"
echo
echo "Heslo je uložené v /etc/squid/harvest.pass. Nové heslo: PROXY_PASSWORD=… bash $0"
