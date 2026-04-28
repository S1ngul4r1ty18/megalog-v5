#!/usr/bin/env bash
# MegaLog v5 — instalador interativo
#
# Substitui setup.sh do v4 (432 linhas) com fluxo mais enxuto:
#   - perguntas mínimas (paths, portas, SECRET_KEY)
#   - usuário sistema dedicado (rootless)
#   - venv local em INSTALL_DIR/.venv
#   - frontend já vem buildado (ou roda npm build aqui)
#   - configura systemd units por substituição de placeholders
#   - habilita/inicia: receiver, processor, web + 3 timers
#
# Suporta dry-run (--dry-run): mostra o que faria sem alterar o sistema.

set -euo pipefail

# ── defaults (sobrescrevíveis via env ou prompt) ─────────────────────────────
: "${INSTALL_DIR:=/usr/local/src/megalog}"
: "${MEGALOG_USER:=megalog}"
: "${CONFIG_DIR:=/etc/megalog}"
: "${CONFIG_FILE:=$CONFIG_DIR/megalog.env}"
: "${HOT_DIR:=/dados1/hot}"
: "${COLD_DIR:=/dados2/cold}"
: "${STREAM_DIR:=/dados1/stream}"
: "${STATE_DIR:=/dados1/state}"
: "${BACKUP_DIR:=/dados2/backups}"
: "${RECEIVER_PORT:=514}"
: "${WEB_PORT:=5000}"
: "${HOT_RETENTION_DAYS:=30}"
: "${DELETE_AFTER_DAYS:=365}"

DRY_RUN=0
NON_INTERACTIVE=0

for arg in "$@"; do
  case "$arg" in
    --dry-run)         DRY_RUN=1 ;;
    --non-interactive) NON_INTERACTIVE=1 ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0 ;;
    *)
      echo "argumento desconhecido: $arg" >&2; exit 2 ;;
  esac
done

# ── helpers ──────────────────────────────────────────────────────────────────
red()    { printf "\033[31m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
blue()   { printf "\033[34m%s\033[0m\n" "$*"; }

step() { blue ">>> $*"; }

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    yellow "DRY-RUN: $*"
  else
    eval "$@"
  fi
}

ask() {
  local prompt="$1" default="$2" var
  if [[ $NON_INTERACTIVE -eq 1 ]]; then
    echo "$default"; return 0
  fi
  read -r -p "$prompt [$default]: " var
  echo "${var:-$default}"
}

# ── pré-checks ───────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  red "Precisa rodar como root (sudo $0)"; exit 1
fi

if ! command -v systemctl >/dev/null; then
  red "systemd não encontrado — distros sem systemd não são suportadas"; exit 1
fi

if [[ ! -f "$INSTALL_DIR/pyproject.toml" ]]; then
  red "INSTALL_DIR=$INSTALL_DIR não tem pyproject.toml — verifique o caminho"; exit 1
fi

# ── pacotes do SO ────────────────────────────────────────────────────────────
step "Instalando dependências do SO (apt)"
APT_PKGS=(python3 python3-venv python3-dev python3-pip nginx)
if command -v apt-get >/dev/null; then
  run "apt-get update -qq"
  run "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ${APT_PKGS[*]}"
else
  yellow "apt-get não encontrado — instale manualmente: ${APT_PKGS[*]}"
fi

# ── perguntas interativas ────────────────────────────────────────────────────
if [[ $NON_INTERACTIVE -eq 0 ]]; then
  green "═══════════════════════════════════════════════════════════════════"
  green "  MegaLog v5 — instalação interativa"
  green "═══════════════════════════════════════════════════════════════════"
  echo "Pressione Enter para aceitar o valor padrão entre [colchetes]."; echo
fi

INSTALL_DIR=$(ask        "Diretório do código-fonte"        "$INSTALL_DIR")
HOT_DIR=$(ask            "Hot storage (SSD)"                "$HOT_DIR")
COLD_DIR=$(ask           "Cold storage (HD)"                "$COLD_DIR")
STREAM_DIR=$(ask         "Buffer .raw"                      "$STREAM_DIR")
STATE_DIR=$(ask          "State (registry, megalog.db)"     "$STATE_DIR")
BACKUP_DIR=$(ask         "Backups (snapshots semanais)"     "$BACKUP_DIR")
RECEIVER_PORT=$(ask      "Porta UDP do syslog"              "$RECEIVER_PORT")
WEB_PORT=$(ask           "Porta TCP da API"                 "$WEB_PORT")
HOT_RETENTION_DAYS=$(ask "Dias em hot antes de mover p/cold" "$HOT_RETENTION_DAYS")
DELETE_AFTER_DAYS=$(ask  "Dias antes de deletar (0=nunca)"  "$DELETE_AFTER_DAYS")

# SECRET_KEY: gera 64 bytes random (não pergunta)
SECRET_KEY="${SECRET_KEY:-$(head -c 48 /dev/urandom | base64 | tr -d '\n')}"

# ── usuário sistema ──────────────────────────────────────────────────────────
step "Criando usuário '$MEGALOG_USER' (sistema, sem login)"
if ! id -u "$MEGALOG_USER" >/dev/null 2>&1; then
  run "useradd --system --no-create-home --shell /usr/sbin/nologin '$MEGALOG_USER'"
else
  yellow "  usuário já existe"
fi

# ── diretórios ───────────────────────────────────────────────────────────────
step "Criando diretórios e ajustando permissões"
for d in "$HOT_DIR" "$COLD_DIR" "$STREAM_DIR" "$STATE_DIR" "$BACKUP_DIR" "$CONFIG_DIR"; do
  run "mkdir -p '$d'"
done
run "chown -R '$MEGALOG_USER:$MEGALOG_USER' '$HOT_DIR' '$COLD_DIR' '$STREAM_DIR' '$STATE_DIR' '$BACKUP_DIR'"

# ── arquivo de configuração ──────────────────────────────────────────────────
step "Gerando $CONFIG_FILE"
TMPCFG=$(mktemp)
cat > "$TMPCFG" <<EOF
# MegaLog v5 — configuração (lida pelos serviços systemd via EnvironmentFile)
# Editar com cuidado; reiniciar serviços após mudanças (systemctl restart megalog-*).

MEGALOG_RECEIVER_HOST=0.0.0.0
MEGALOG_RECEIVER_PORT=$RECEIVER_PORT
# Bind do uvicorn em loopback — nginx faz proxy. Não exponha 0.0.0.0 em LAN
# pública (a 5000 ficaria acessível direto, contornando rate-limit do nginx).
MEGALOG_WEB_HOST=127.0.0.1
MEGALOG_WEB_PORT=$WEB_PORT

MEGALOG_HOT_STORAGE_DIR=$HOT_DIR
MEGALOG_COLD_STORAGE_DIR=$COLD_DIR
MEGALOG_STREAM_DIR=$STREAM_DIR
MEGALOG_STATE_DIR=$STATE_DIR
# IP registry usa SQLite WAL (permite N readers + 1 writer concorrentes;
# DuckDB tem lock exclusivo e não funcionaria aqui).
MEGALOG_IP_REGISTRY_PATH=$STATE_DIR/ip_registry.db

MEGALOG_HOT_RETENTION_DAYS=$HOT_RETENTION_DAYS
MEGALOG_DELETE_AFTER_DAYS=$DELETE_AFTER_DAYS

MEGALOG_SECRET_KEY=$SECRET_KEY
MEGALOG_DUCKDB_CLOSE_INTERVAL_SECONDS=3.0
EOF
if [[ $DRY_RUN -eq 1 ]]; then
  yellow "DRY-RUN: criaria $CONFIG_FILE com:"
  sed 's/^/  | /' "$TMPCFG"
  rm -f "$TMPCFG"
else
  install -m 0640 -o root -g "$MEGALOG_USER" "$TMPCFG" "$CONFIG_FILE"
  rm -f "$TMPCFG"
fi

# ── venv Python ──────────────────────────────────────────────────────────────
step "Criando .venv e instalando dependências Python"
run "python3 -m venv '$INSTALL_DIR/.venv'"
run "'$INSTALL_DIR/.venv/bin/pip' install --quiet --upgrade pip"
run "'$INSTALL_DIR/.venv/bin/pip' install --quiet -e '$INSTALL_DIR'"

# ── frontend (build se ainda não existe) ─────────────────────────────────────
if [[ ! -d "$INSTALL_DIR/frontend/dist" ]]; then
  step "Frontend não buildado — rodando 'npm install && npm run build'"
  if ! command -v npm >/dev/null; then
    run "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs npm"
  fi
  run "(cd '$INSTALL_DIR/frontend' && npm install --no-fund --no-audit && npm run build)"
else
  step "Frontend já buildado em $INSTALL_DIR/frontend/dist (pulando build)"
fi

# ── permissões do código ─────────────────────────────────────────────────────
run "chown -R '$MEGALOG_USER:$MEGALOG_USER' '$INSTALL_DIR/.venv'"

# ── unit files systemd ───────────────────────────────────────────────────────
step "Instalando unit files systemd"
TMPL_DIR="$INSTALL_DIR/deploy/systemd"
TARGET_DIR="/etc/systemd/system"
for f in megalog-receiver.service megalog-processor.service megalog-web.service \
         megalog-archive.service  megalog-archive.timer \
         megalog-analyze.service  megalog-analyze.timer \
         megalog-retention.service megalog-retention.timer \
         megalog-backup.service   megalog-backup.timer \
         megalog-stream-watchdog.service megalog-stream-watchdog.timer; do
  src="$TMPL_DIR/$f"
  dst="$TARGET_DIR/$f"
  if [[ ! -f "$src" ]]; then
    red "  template ausente: $src"; exit 1
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    yellow "DRY-RUN: instalaria $dst (com placeholders substituídos)"
  else
    sed \
      -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
      -e "s|@CONFIG_FILE@|$CONFIG_FILE|g" \
      -e "s|@MEGALOG_USER@|$MEGALOG_USER|g" \
      -e "s|@HOT_DIR@|$HOT_DIR|g" \
      -e "s|@COLD_DIR@|$COLD_DIR|g" \
      -e "s|@STREAM_DIR@|$STREAM_DIR|g" \
      -e "s|@STATE_DIR@|$STATE_DIR|g" \
      -e "s|@BACKUP_DIR@|$BACKUP_DIR|g" \
      "$src" > "$dst"
    chmod 0644 "$dst"
  fi
done

# ── nginx ────────────────────────────────────────────────────────────────────
step "Configurando nginx (site megalog em /etc/nginx/sites-available/)"
if [[ $DRY_RUN -eq 1 ]]; then
  yellow "DRY-RUN: removeria /etc/nginx/sites-enabled/default (host dedicado ao MegaLog)"
  yellow "DRY-RUN: instalaria /etc/nginx/sites-available/megalog (apontando para $INSTALL_DIR/frontend/dist e proxy /api → 127.0.0.1:$WEB_PORT)"
else
  # Host é dedicado ao MegaLog — remove o site default do Debian para evitar
  # conflito de `default_server` em :80.
  if [[ -L /etc/nginx/sites-enabled/default ]]; then
    rm -f /etc/nginx/sites-enabled/default
    yellow "  removido /etc/nginx/sites-enabled/default (conflito de default_server)"
  fi
  # Rate-limit zone (precisa estar em http{} — vai em conf.d/)
  install -m 0644 "$INSTALL_DIR/deploy/nginx-ratelimit.conf" \
                   /etc/nginx/conf.d/megalog-ratelimit.conf
  sed -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
      -e "s|@WEB_PORT@|$WEB_PORT|g" \
      "$INSTALL_DIR/deploy/nginx.conf.example" > /etc/nginx/sites-available/megalog
  ln -sf /etc/nginx/sites-available/megalog /etc/nginx/sites-enabled/megalog
  if nginx -t 2>/dev/null; then
    systemctl reload nginx 2>/dev/null || systemctl restart nginx
    green "  nginx OK e recarregado"
  else
    red "  nginx -t falhou — saída detalhada:"
    nginx -t 2>&1 | sed 's/^/    /'
  fi
fi

# ── habilitar serviços ───────────────────────────────────────────────────────
step "Habilitando e iniciando serviços systemd"
run "systemctl daemon-reload"
SERVICES=(megalog-receiver megalog-processor megalog-web)
TIMERS=(megalog-archive.timer megalog-analyze.timer megalog-retention.timer
        megalog-backup.timer  megalog-stream-watchdog.timer)
for s in "${SERVICES[@]}"; do
  run "systemctl enable --now '$s'"
done
for t in "${TIMERS[@]}"; do
  run "systemctl enable --now '$t'"
done

# ── status final ─────────────────────────────────────────────────────────────
green "═══════════════════════════════════════════════════════════════════"
green "  Instalação concluída"
green "═══════════════════════════════════════════════════════════════════"
echo
echo "  Web:           http://<este-host>:$WEB_PORT  (proxy nginx :80)"
echo "  Login default: admin / megalog123  (TROCAR no primeiro acesso)"
echo "  UDP syslog:    porta $RECEIVER_PORT (configurar Mikrotik para apontar aqui)"
echo "  Config file:   $CONFIG_FILE"
echo
echo "  Serviços:"
for s in "${SERVICES[@]}" "${TIMERS[@]}"; do
  echo "    systemctl status $s"
done
echo
echo "  Logs:"
echo "    journalctl -u megalog-receiver -f"
echo "    journalctl -u megalog-processor -f"
echo "    journalctl -u megalog-web -f"
echo
