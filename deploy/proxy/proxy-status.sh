#!/usr/bin/env bash
# Přehled VPS s proxy pro sběr úředních desek: služby, proxy, SSH tunel, squid, fail2ban, systém.
# Jen čte – nic nemění (squid, SSH, hesla ani klíče). Plné údaje (log squidu, fail2ban, journal) vidí root.
#
# Instalace (jednou, jako root; nezávislá na setup-squid-debian12.sh):
#   bash proxy-status.sh --install
# → /usr/local/bin/proxy-status a výpis po každém přihlášení (/etc/profile.d/uredni-desky-status.sh).
# Ručně: proxy-status   (rychle, bez testu přes proxy: proxy-status --quick)
# Odinstalace: rm /usr/local/bin/proxy-status /etc/profile.d/uredni-desky-status.sh

set -u
QUICK=""
[[ "${1:-}" == "--quick" ]] && QUICK=1

if [[ "${1:-}" == "--install" ]]; then
  if [[ $EUID -ne 0 ]]; then echo "Instalace jako root: sudo bash $0 --install" >&2; exit 1; fi
  install -m 755 "$0" /usr/local/bin/proxy-status
  cat > /etc/profile.d/uredni-desky-status.sh <<'HOOK'
# Přehled proxy po přihlášení (jen interaktivní shell; vypnout: smazat tento soubor)
case $- in *i*) [ -x /usr/local/bin/proxy-status ] && /usr/local/bin/proxy-status --quick ;; esac
HOOK
  chmod 644 /etc/profile.d/uredni-desky-status.sh
  echo "Nainstalováno: příkaz proxy-status a přehled po přihlášení."
  exit 0
fi

PORT="${PROXY_PORT:-3128}"
TUNNEL_USER="${TUNNEL_USER:-harvest-tunnel}"
PROXY_USER="${PROXY_USER:-harvest}"
TEST_URL="${PROXY_TEST_URL:-https://www.mujesenice.cz/}"

if [[ -t 1 ]]; then
  G=$'\e[32m' R=$'\e[31m' Y=$'\e[33m' B=$'\e[1m' D=$'\e[2m' N=$'\e[0m'
else
  G="" R="" Y="" B="" D="" N=""
fi
# zarovnání podle počtu znaků, ne bajtů (diakritika) – proto UTF-8 locale a vlastní doplnění mezerami
export LC_ALL=C.UTF-8
W=26
pad()  { local s="$1"; printf "%s%*s" "$s" $(( W - ${#s} > 1 ? W - ${#s} : 1 )) ""; }
ok()   { printf "  %s●%s %s%s\n" "$G" "$N" "$(pad "$1")" "${2:-}"; }
bad()  { printf "  %s●%s %s%s\n" "$R" "$N" "$(pad "$1")" "${2:-}"; }
warn() { printf "  %s●%s %s%s\n" "$Y" "$N" "$(pad "$1")" "${2:-}"; }
info() { printf "    %s%s\n" "$(pad "$1")" "${2:-}"; }
head_() { printf "\n%s%s%s\n" "$B" "$1" "$N"; }
IS_ROOT=""; [[ $EUID -eq 0 ]] && IS_ROOT=1

# --- systém ---
printf "%s%s – proxy pro sběr úředních desek%s   %s%s%s\n" "$B" "$(hostname)" "$N" "$D" "$(date '+%-d. %-m. %Y %H:%M')" "$N"
head_ "Systém"
info "běží" "$(uptime -p 2>/dev/null | sed 's/^up //')"
info "zátěž (1/5/15 min)" "$(cut -d' ' -f1-3 /proc/loadavg) (CPU: $(nproc))"
info "paměť" "$(free -h | awk '/^Mem:/ {print $3 " z " $2 " (volno " $7 ")"}')"
disk_pct="$(df -P / | awk 'NR==2 {print $5}' | tr -d %)"
disk_txt="$(df -hP / | awk 'NR==2 {print $3 " z " $2 " (" $5 ")"}')"
if (( disk_pct >= 90 )); then bad "disk /" "$disk_txt"; elif (( disk_pct >= 75 )); then warn "disk /" "$disk_txt"; else info "disk /" "$disk_txt"; fi
if [[ -f /var/run/reboot-required ]]; then warn "restart" "po aktualizacích je potřeba restart (reboot)"; fi

# --- služby ---
head_ "Služby"
for svc in squid ssh fail2ban nftables unattended-upgrades; do
  state="$(systemctl is-active "$svc" 2>/dev/null)"
  since="$(systemctl show -p ActiveEnterTimestamp --value "$svc" 2>/dev/null)"
  since="${since:+od $(date -d "$since" '+%-d. %-m. %H:%M' 2>/dev/null)}"
  if [[ "$state" == "active" ]]; then ok "$svc" "běží $since"
  elif [[ -z "$state" || "$state" == "unknown" ]]; then warn "$svc" "není nainstalovaná"
  else bad "$svc" "$state"; fi
done

# --- proxy ---
head_ "Proxy (squid)"
if command -v ss >/dev/null; then
  listen="$(ss -Hltn "sport = :$PORT" 2>/dev/null | awk '{print $4}' | sort -u)"
elif (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  listen="127.0.0.1:$PORT"  # bez ss: aspoň že na localhostu odpovídá
else
  listen=""
fi
if [[ -z "$listen" ]]; then
  bad "naslouchá" "na portu $PORT nic neposlouchá"
elif grep -qvE '^(127\.0\.0\.1|\[::1\]):' <<<"$listen"; then
  bad "naslouchá" "$(tr '\n' ' ' <<<"$listen")– i mimo localhost! (má být jen 127.0.0.1)"
else
  ok "naslouchá" "$(tr '\n' ' ' <<<"$listen")(jen lokálně, zvenku přes SSH tunel)"
fi
if [[ -z "$QUICK" && -n "$IS_ROOT" && -r /etc/squid/harvest.pass ]]; then
  code="$(curl -s -o /dev/null -w '%{http_code} za %{time_total} s' --max-time 8 \
    -x "http://${PROXY_USER}:$(cat /etc/squid/harvest.pass)@127.0.0.1:${PORT}" \
    -A 'uredni-desky-proxy-status' "$TEST_URL" 2>/dev/null)"
  if [[ "$code" == 2* || "$code" == 3* ]]; then ok "test přes proxy" "$code ($TEST_URL)"
  else bad "test přes proxy" "${code:-bez odpovědi} ($TEST_URL)"; fi
fi
LOG=/var/log/squid/access.log
if [[ -r "$LOG" ]]; then
  now=$(date +%s)
  # formát logu squid: čas trvání klient kód/stav bajty metoda URL …  (awk bez rozšíření gawk – Debian má mawk)
  awk -v t=$((now - 3600)) -v d=$((now - 86400)) '
    $1 >= d { day++; if ($4 ~ /DENIED/) dden++ }
    $1 >= t { hour++; if ($4 ~ /DENIED/) hden++ }
    END {
      printf "%d %d %d %d\n", hour + 0, hden + 0, day + 0, dden + 0
    }' "$LOG" | { read -r h hd d dd; info "požadavky za hodinu" "$h (odmítnuto $hd)"; info "požadavky za 24 h" "$d (odmítnuto $dd)"; }
  top="$(awk -v d=$((now - 86400)) '$1 >= d { split($7, u, "[/:]"); print ($6 == "CONNECT") ? u[1] : u[4] }' "$LOG" \
    | sort | uniq -c | sort -rn | head -n 5 | awk '{printf "%s%s %s", (NR > 1 ? ", " : ""), $2, $1}')"
  [[ -n "$top" ]] && info "nejčastější weby (24 h)" "$top"
  last="$(tail -n 1 "$LOG" 2>/dev/null | awk '{print $1}')"
  [[ -n "$last" ]] && info "poslední požadavek" "$(date -d "@${last%.*}" '+%-d. %-m. %H:%M:%S')"
else
  info "log squidu" "nečitelný (spusť jako root)"
fi

# --- SSH tunel z GitHub Actions ---
head_ "SSH tunel ($TUNNEL_USER)"
if id "$TUNNEL_USER" >/dev/null 2>&1; then
  live="$(pgrep -c -u "$TUNNEL_USER" sshd 2>/dev/null || echo 0)"
  if (( live > 0 )); then ok "právě připojeno" "$live (běží sběr)"; else info "právě připojeno" "nikdo"; fi
  if [[ -n "$IS_ROOT" ]] && command -v journalctl >/dev/null; then
    acc="$(journalctl -q -u ssh -u sshd --since '-24 h' -o short-unix 2>/dev/null | grep "Accepted publickey for $TUNNEL_USER")"
    n=$(grep -c . <<<"$acc")
    lastc="$(tail -n 1 <<<"$acc" | awk '{print $1}')"
    if [[ -n "$lastc" ]]; then
      info "připojení za 24 h" "$n, poslední $(date -d "@${lastc%.*}" '+%-d. %-m. %H:%M')"
      age=$(( $(date +%s) - ${lastc%.*} ))
      (( age > 3 * 3600 )) && warn "sběr" "poslední připojení před $((age / 3600)) h – běží hodinový sběr?"
    else
      warn "připojení za 24 h" "žádné – běží sběr a jsou nastavené secrety PROXY_SSH_*?"
    fi
  fi
else
  bad "uživatel" "$TUNNEL_USER neexistuje (tunel nastavuje setup-squid-debian12.sh)"
fi

# --- zabezpečení ---
head_ "Zabezpečení"
if [[ -n "$IS_ROOT" ]] && command -v sshd >/dev/null; then
  pa="$(sshd -T 2>/dev/null | awk '/^passwordauthentication/ {print $2}')"
  prl="$(sshd -T 2>/dev/null | awk '/^permitrootlogin/ {print $2}')"
  if [[ "$pa" == "no" ]]; then ok "SSH hesla" "vypnutá (jen klíče)"; else warn "SSH hesla" "přihlášení heslem je povolené (doporučeno jen klíčem)"; fi
  info "root přes SSH" "$prl"
fi
if [[ -n "$IS_ROOT" ]] && command -v nft >/dev/null; then
  if nft list chain inet filter input 2>/dev/null | grep -q "dport $PORT"; then bad "firewall" "port $PORT je otevřený zvenku!"
  elif nft list chain inet filter input 2>/dev/null | grep -q "policy drop"; then ok "firewall" "dovnitř jen SSH"
  else warn "firewall" "pravidla nftables nejsou načtená"; fi
fi
if [[ -n "$IS_ROOT" ]] && command -v fail2ban-client >/dev/null; then
  f2b="$(fail2ban-client status sshd 2>/dev/null)"
  cur="$(awk -F': *' '/Currently banned/ {print $2}' <<<"$f2b")"
  tot="$(awk -F': *' '/Total banned/ {print $2}' <<<"$f2b")"
  [[ -n "$cur" ]] && info "fail2ban (sshd)" "zablokováno teď ${cur}, celkem ${tot:-?}"
fi
if [[ -r /var/log/unattended-upgrades/unattended-upgrades.log ]]; then
  lu="$(grep -h "INFO" /var/log/unattended-upgrades/unattended-upgrades.log 2>/dev/null | tail -n 1 | cut -d' ' -f1,2 | cut -d, -f1)"
  [[ -n "$lu" ]] && info "automatické aktualizace" "naposledy $(date -d "$lu" '+%-d. %-m. %H:%M' 2>/dev/null || echo "$lu")"
fi
echo
