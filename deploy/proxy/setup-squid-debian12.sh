#!/usr/bin/env bash
# Záložní proxy v ČR pro sběr úředních desek (HARVEST_FALLBACK_PROXY).
#
# Pro čerstvý Debian 12 nebo 13 na VPS s českou IP. Spustit jako root:
#   bash setup-squid-debian12.sh
# Volitelně: PORT=3128 SSH_PORT=22 EXTRA_DOMAINS=".obec.cz .jina-obec.cz" bash setup-squid-debian12.sh
#
# Co nastaví:
#   - squid s přihlášením jménem a heslem (heslo se vygeneruje, nebo se použije PROXY_PASSWORD),
#     poslouchá jen na 127.0.0.1 – z internetu je dostupný jen šifrovaným SSH tunelem
#   - uživatele harvest-tunnel: přihlášení jen klíčem, bez shellu, smí jen tunel na 127.0.0.1:PORT
#   - SSH jen s klíči (bez hesel, root jen klíčem) – pokud už root nebo uživatel ze skupiny sudo klíč má;
#     jinak hesla nechá a varuje, aby ses nezamkl
#   - fail2ban pro SSH
#   - jen weby obcí mikroregionu (žádná otevřená proxy), jen porty 80/443, bez mezipaměti
#   - firewall nftables: dovnitř jen SSH
#   - automatické bezpečnostní aktualizace
# Na konci vypíše hodnoty secretů pro GitHub (PROXY_SSH_KEY, PROXY_SSH_KNOWN_HOSTS, PROXY_SSH_DEST,
# HARVEST_FALLBACK_PROXY). Workflow si tunel otevřou samy (.github/actions/proxy-tunnel).
# Skript jde spustit znovu (např. po přidání domény); heslo zůstane, pokud nezadáš nové.
set -euo pipefail

PORT="${PORT:-3128}"
SSH_PORT="${SSH_PORT:-22}"
PROXY_USER="${PROXY_USER:-harvest}"
TUNNEL_USER="${TUNNEL_USER:-harvest-tunnel}"
# Weby obcí Dolnobřežanska (tečka na začátku = i subdomény, např. www.)
DOMAINS=".vestec.cz .obecvestec.cz .mujesenice.cz .okrouhlo.cz .zlatniky-hodkovice.cz .brezova-olesko.cz
.ohrobec.cz .liber.cz .zvole.info .psary.cz .dolnibrezany.cz .vranenadvltavou.cz .imunis.cz .alis.cz ${EXTRA_DOMAINS:-}"

if [[ $EUID -ne 0 ]]; then echo "Spusť jako root (sudo bash $0)." >&2; exit 1; fi
if ! grep -qE 'VERSION_ID="(12|13)"' /etc/os-release; then echo "Upozornění: skript je psaný pro Debian 12 a 13." >&2; fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq squid apache2-utils nftables unattended-upgrades curl openssl openssh-server \
  fail2ban python3-systemd >/dev/null

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
# jen lokálně: z internetu vede k proxy jen SSH tunel uživatele ${TUNNEL_USER}
http_port 127.0.0.1:${PORT}

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

# --- uživatel pro SSH tunel ---
# Bez shellu a bez hesla; klíč smí jen přesměrovat port na 127.0.0.1:PORT (authorized_keys i sshd Match).
if ! id "$TUNNEL_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$TUNNEL_USER"
fi
# „*“ = žádné platné heslo; „!“ (zamčený účet) by sshd bez PAM odmítl i s klíčem
usermod -p '*' "$TUNNEL_USER"
TUNNEL_HOME="$(getent passwd "$TUNNEL_USER" | cut -d: -f6)"
install -d -m 700 /etc/squid/tunnel
if [[ ! -f /etc/squid/tunnel/id_ed25519 ]]; then
  ssh-keygen -q -t ed25519 -N "" -C "github-actions-proxy-tunnel" -f /etc/squid/tunnel/id_ed25519
fi
chmod 600 /etc/squid/tunnel/id_ed25519
install -d -m 700 -o "$TUNNEL_USER" -g "$TUNNEL_USER" "$TUNNEL_HOME/.ssh"
printf 'restrict,port-forwarding,permitopen="127.0.0.1:%s",command="/bin/false" %s\n' \
  "$PORT" "$(cat /etc/squid/tunnel/id_ed25519.pub)" > "$TUNNEL_HOME/.ssh/authorized_keys"
chown "$TUNNEL_USER:$TUNNEL_USER" "$TUNNEL_HOME/.ssh/authorized_keys"
chmod 600 "$TUNNEL_HOME/.ssh/authorized_keys"

# --- SSH: jen klíče, tunelový uživatel jen přesměrování ---
# Přihlášení heslem se vypne, jen pokud už má root nebo některý uživatel ze skupiny sudo klíč – jinak by
# ses zamkl. V tom případě skript varuje a hesla nechá zapnutá (vlož klíč a spusť skript znovu).
HAS_ADMIN_KEY=""
ADMIN_HOMES="/root"
for u in $(getent group sudo | cut -d: -f4 | tr ',' ' '); do
  ADMIN_HOMES="$ADMIN_HOMES $(getent passwd "$u" | cut -d: -f6)"
done
for home in $ADMIN_HOMES; do
  if [[ -s "$home/.ssh/authorized_keys" ]]; then HAS_ADMIN_KEY=1; fi
done
install -d -m 755 /etc/ssh/sshd_config.d
{
  echo "# Spravuje setup-squid-debian12.sh"
  if [[ -n "$HAS_ADMIN_KEY" ]]; then
    echo "PasswordAuthentication no"
    echo "KbdInteractiveAuthentication no"
    echo "PermitRootLogin prohibit-password"
  fi
  echo "Match User ${TUNNEL_USER}"
  echo "  AuthenticationMethods publickey"
  echo "  AllowTcpForwarding local"
  echo "  PermitOpen 127.0.0.1:${PORT}"
  echo "  PermitListen none"
  echo "  X11Forwarding no"
  echo "  AllowAgentForwarding no"
  echo "  PermitTTY no"
  echo "  ForceCommand /bin/false"
} > /etc/ssh/sshd_config.d/10-uredni-desky.conf
if ! sshd -t; then
  echo "Chyba v nastavení SSH – vracím zpět." >&2
  rm -f /etc/ssh/sshd_config.d/10-uredni-desky.conf
  exit 1
fi
systemctl reload ssh 2>/dev/null || systemctl reload sshd
if [[ -z "$HAS_ADMIN_KEY" ]]; then
  echo "UPOZORNĚNÍ: root ani uživatel ze skupiny sudo nemá SSH klíč – přihlášení heslem zůstává zapnuté." >&2
  echo "            Vlož svůj veřejný klíč do ~/.ssh/authorized_keys a spusť skript znovu." >&2
fi

# --- fail2ban pro SSH ---
cat > /etc/fail2ban/jail.d/uredni-desky.local <<F2B
# Spravuje setup-squid-debian12.sh
[sshd]
enabled = true
port = ${SSH_PORT}
backend = systemd
banaction = nftables-multiport
maxretry = 5
findtime = 10m
bantime = 1h
F2B
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

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
HOSTKEY="$(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"
if [[ "$SSH_PORT" == "22" ]]; then KH_HOST="$IP"; else KH_HOST="[$IP]:$SSH_PORT"; fi
echo "Do GitHubu: Settings → Secrets and variables → Actions → Secrets (New repository secret / Update):"
echo
echo "  PROXY_SSH_DEST          ssh://${TUNNEL_USER}@${IP}:${SSH_PORT}"
echo "  PROXY_SSH_KNOWN_HOSTS   ${KH_HOST} ${HOSTKEY}"
echo "  HARVEST_FALLBACK_PROXY  http://${PROXY_USER}:${PASS}@127.0.0.1:${PORT}"
echo "  PROXY_SSH_KEY           celý soukromý klíč níže, včetně řádků BEGIN a END:"
echo
cat /etc/squid/tunnel/id_ed25519
echo
echo "Starou HARVEST_FALLBACK_PROXY (s veřejnou IP) nahraď hodnotou výše – port ${PORT} už z internetu nejde."
echo "Klíč tunelu je v /etc/squid/tunnel/id_ed25519; nový klíč: smaž ten soubor a spusť skript znovu."
echo "Heslo je uložené v /etc/squid/harvest.pass. Nové heslo: PROXY_PASSWORD=… bash $0"
