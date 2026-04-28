#!/usr/bin/env bash
# MegaLog v5 — desinstalação
#
# Remove em ordem segura:
#   1. para serviços e timers
#   2. desabilita units
#   3. apaga unit files
#   4. remove site nginx
#   5. (opcional) remove dados, config e usuário do sistema
#
# Por padrão NÃO apaga dados. Use --purge para apagar tudo.

set -euo pipefail

PURGE=0
NON_INTERACTIVE=0
for arg in "$@"; do
  case "$arg" in
    --purge)           PURGE=1 ;;
    --non-interactive) NON_INTERACTIVE=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
    *)
      echo "argumento desconhecido: $arg" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Precisa rodar como root (sudo $0)" >&2; exit 1
fi

: "${MEGALOG_USER:=megalog}"
: "${CONFIG_DIR:=/etc/megalog}"
: "${HOT_DIR:=/dados1/hot}"
: "${COLD_DIR:=/dados2/cold}"
: "${STREAM_DIR:=/dados1/stream}"
: "${STATE_DIR:=/dados1/state}"
: "${BACKUP_DIR:=/dados2/backups}"

confirm() {
  if [[ $NON_INTERACTIVE -eq 1 ]]; then return 0; fi
  read -r -p "$1 [y/N]: " ans
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]
}

UNITS=(megalog-receiver  megalog-processor  megalog-web)
TIMERS=(megalog-archive.timer megalog-analyze.timer megalog-retention.timer
        megalog-backup.timer  megalog-stream-watchdog.timer)
ONESHOTS=(megalog-archive.service megalog-analyze.service megalog-retention.service
          megalog-backup.service  megalog-stream-watchdog.service)

echo ">>> Parando serviços e timers"
for u in "${UNITS[@]}" "${TIMERS[@]}"; do
  systemctl stop    "$u" 2>/dev/null || true
  systemctl disable "$u" 2>/dev/null || true
done

echo ">>> Removendo unit files"
for f in "${UNITS[@]}" "${TIMERS[@]}" "${ONESHOTS[@]}"; do
  rm -f "/etc/systemd/system/$f.service" "/etc/systemd/system/$f"
done
systemctl daemon-reload

echo ">>> Removendo site nginx + rate-limit conf"
rm -f /etc/nginx/sites-enabled/megalog /etc/nginx/sites-available/megalog \
      /etc/nginx/conf.d/megalog-ratelimit.conf
if command -v nginx >/dev/null && nginx -t 2>/dev/null; then
  systemctl reload nginx 2>/dev/null || true
fi

if [[ $PURGE -eq 1 ]]; then
  if confirm "ATENÇÃO: --purge irá APAGAR config + dados em $HOT_DIR, $COLD_DIR, $STREAM_DIR, $STATE_DIR, $BACKUP_DIR. Confirma?"; then
    echo ">>> Apagando dados e config"
    rm -rf "$HOT_DIR" "$COLD_DIR" "$STREAM_DIR" "$STATE_DIR" "$BACKUP_DIR" "$CONFIG_DIR"
    if id -u "$MEGALOG_USER" >/dev/null 2>&1; then
      userdel "$MEGALOG_USER" 2>/dev/null || true
    fi
  else
    echo "  abortado --purge (dados preservados)"
  fi
else
  echo ">>> Dados preservados em $HOT_DIR, $COLD_DIR, $STREAM_DIR, $STATE_DIR, $BACKUP_DIR"
  echo "    (use --purge para apagar tudo)"
fi

echo
echo "Desinstalação concluída."
