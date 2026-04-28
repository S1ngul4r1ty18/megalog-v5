# Instalação do MegaLog v5

> Para sysadmin instalando do zero ou atualizando.

## Índice

1. [Requisitos](#1-requisitos)
2. [Instalação rápida (recomendada)](#2-instalação-rápida-recomendada)
3. [Opções do install.sh](#3-opções-do-installsh)
4. [Instalação manual passo-a-passo](#4-instalação-manual-passo-a-passo)
5. [Configurar o Mikrotik](#5-configurar-o-mikrotik)
6. [Verificação pós-instalação](#6-verificação-pós-instalação)
7. [Atualização (upgrade do código)](#7-atualização-upgrade-do-código)
8. [Desinstalação](#8-desinstalação)

---

## 1. Requisitos

### Sistema operacional
- Debian 12+ ou Ubuntu 22.04+ (testado em Debian 13 trixie)
- systemd disponível
- Acesso root (`sudo`)

### Hardware

| Recurso | Mínimo | Recomendado |
|---|---|---|
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8+ GB |
| Disco hot (SSD) | 5 GB/mês por roteador | NVMe |
| Disco cold (HDD) | 1 GB/mês por roteador | 7200 RPM |

**Cálculo de capacidade:** ~9 bytes/log no Parquet zstd. Roteador típico CGNAT
com 1M logs/dia → 9 MB/dia → ~3.3 GB/ano (cold). Hot precisa de 30× isso (~270 MB/dia).

### Rede

- Porta UDP 514 acessível dos Mikrotiks (ou outra, configurável)
- Porta TCP 80 (nginx, UI web)
- Porta TCP 5000 só localhost (nginx faz proxy) — opcionalmente exposta
- Porta TCP 22 (SSH para admin)

### Pacotes do SO (instalados pelo install.sh)

`python3` (3.11+) · `python3-venv` · `python3-dev` · `python3-pip` · `nginx` · `nodejs` · `npm`

---

## 2. Instalação rápida (recomendada)

```bash
# 1. Copiar o código fonte para /usr/local/src/megalog-v5/
#    (assumindo que você já tem o repo aqui)

# 2. Rodar o instalador interativo
sudo /usr/local/src/megalog-v5/deploy/install.sh
```

O instalador é interativo e pergunta:

| Pergunta | Default | Notas |
|---|---|---|
| Diretório do código-fonte | `/usr/local/src/megalog-v5` | Onde está o repo |
| Hot storage (SSD) | `/dados1/hot` | DuckDB do dia atual + dias recentes |
| Cold storage (HD) | `/dados2/cold` | Parquet zstd dos dias antigos |
| Buffer .raw | `/dados1/stream` | Arquivos rotacionados por hora |
| State (registry, megalog.db) | `/dados1/state` | SQLite de IPs + ops |
| Porta UDP do syslog | `514` | Mikrotik enviará para esta porta |
| Porta TCP da API | `5000` | Uvicorn (nginx faz proxy) |
| Dias em hot antes de mover p/cold | `30` | Quanto antes, menos espaço SSD |
| Dias antes de deletar (0=nunca) | `365` | Compliance Anatel: 365 mínimo |

Aceite os defaults com Enter para uma instalação padrão. **Tempo: ~2 min** (sem build do frontend) ou **~3-5 min** (se for fazer `npm install && npm run build`).

### O que o install.sh faz (sequência)

1. **`apt-get install`** dos pacotes do SO listados acima
2. **Pergunta interativa** dos paths/portas
3. **`useradd --system megalog`** (sem login, sem home)
4. **`mkdir -p`** dos 4 diretórios + chown para `megalog:megalog`
5. **Gera `/etc/megalog/megalog.env`** com SECRET_KEY 48-byte random + perms `640 root:megalog`
6. **Cria `.venv`** + `pip install -e` do projeto
7. **`npm install && npm run build`** se `frontend/dist/` não existe
8. **`chown` do `.venv`** para `megalog`
9. **Instala 9 unit files** systemd (substitui placeholders `@INSTALL_DIR@/...`)
10. **Configura nginx** (remove site `default` se existir, instala `megalog`)
11. **`systemctl enable --now`** dos 3 services + 3 timers
12. **Mostra status final** com URL, login default e comandos úteis

### Após instalar

- Web: `http://<IP-do-servidor>/` (nginx :80 proxia para uvicorn :5000)
- Login default: **`admin / megalog123`** — **TROCAR no primeiro acesso**
- UDP syslog: porta **514**

---

## 3. Opções do install.sh

```bash
# Modo dry-run (mostra tudo que faria, sem alterar nada)
sudo /usr/local/src/megalog-v5/deploy/install.sh --dry-run

# Modo non-interactive (aceita defaults sem perguntar)
sudo /usr/local/src/megalog-v5/deploy/install.sh --non-interactive

# Combinar (CI/CD)
sudo /usr/local/src/megalog-v5/deploy/install.sh --dry-run --non-interactive
```

### Sobrescrever defaults via env

```bash
sudo INSTALL_DIR=/opt/megalog \
     HOT_DIR=/mnt/ssd/hot \
     COLD_DIR=/mnt/hdd/cold \
     RECEIVER_PORT=10514 \
     WEB_PORT=8080 \
     /usr/local/src/megalog-v5/deploy/install.sh --non-interactive
```

---

## 4. Instalação manual passo-a-passo

Se preferir entender cada passo (ou se o `install.sh` falhar por algum motivo):

### 4.1. Pacotes do SO

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-dev python3-pip nginx nodejs npm
```

### 4.2. Usuário sistema

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin megalog
```

### 4.3. Diretórios

```bash
sudo mkdir -p /dados1/hot /dados1/stream /dados1/state /dados2/cold /etc/megalog
sudo chown -R megalog:megalog /dados1/hot /dados1/stream /dados1/state /dados2/cold
```

### 4.4. Config

```bash
SECRET=$(head -c 48 /dev/urandom | base64 | tr -d '\n')
sudo tee /etc/megalog/megalog.env > /dev/null <<EOF
MEGALOG_RECEIVER_HOST=0.0.0.0
MEGALOG_RECEIVER_PORT=514
MEGALOG_WEB_HOST=0.0.0.0
MEGALOG_WEB_PORT=5000

MEGALOG_HOT_STORAGE_DIR=/dados1/hot
MEGALOG_COLD_STORAGE_DIR=/dados2/cold
MEGALOG_STREAM_DIR=/dados1/stream
MEGALOG_STATE_DIR=/dados1/state
MEGALOG_IP_REGISTRY_PATH=/dados1/state/ip_registry.db

MEGALOG_HOT_RETENTION_DAYS=30
MEGALOG_DELETE_AFTER_DAYS=365

MEGALOG_SECRET_KEY=$SECRET
EOF
sudo chmod 640 /etc/megalog/megalog.env
sudo chown root:megalog /etc/megalog/megalog.env
```

### 4.5. Python venv + deps

```bash
cd /usr/local/src/megalog-v5
sudo python3 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install -e .
sudo chown -R megalog:megalog .venv
```

### 4.6. Build do frontend

```bash
cd /usr/local/src/megalog-v5/frontend
sudo npm install --no-fund --no-audit
sudo npm run build
```

Output em `frontend/dist/`. nginx vai servir esse diretório.

### 4.7. Unit files systemd

Os templates em `deploy/systemd/*.service` e `*.timer` têm placeholders
`@INSTALL_DIR@`, `@CONFIG_FILE@`, `@MEGALOG_USER@`, `@HOT_DIR@`, `@COLD_DIR@`,
`@STREAM_DIR@`, `@STATE_DIR@`. Substitua com `sed` e instale:

```bash
TMPL=/usr/local/src/megalog-v5/deploy/systemd
TARGET=/etc/systemd/system

for f in megalog-receiver.service megalog-processor.service megalog-web.service \
         megalog-archive.service megalog-archive.timer \
         megalog-analyze.service megalog-analyze.timer \
         megalog-retention.service megalog-retention.timer; do
  sudo sed \
    -e "s|@INSTALL_DIR@|/usr/local/src/megalog-v5|g" \
    -e "s|@CONFIG_FILE@|/etc/megalog/megalog.env|g" \
    -e "s|@MEGALOG_USER@|megalog|g" \
    -e "s|@HOT_DIR@|/dados1/hot|g" \
    -e "s|@COLD_DIR@|/dados2/cold|g" \
    -e "s|@STREAM_DIR@|/dados1/stream|g" \
    -e "s|@STATE_DIR@|/dados1/state|g" \
    "$TMPL/$f" | sudo tee "$TARGET/$f" > /dev/null
done

sudo systemctl daemon-reload
```

### 4.8. nginx

```bash
# Remove o site default do Debian (conflito de default_server)
sudo rm -f /etc/nginx/sites-enabled/default

# Instala nosso site
sudo cp /usr/local/src/megalog-v5/deploy/nginx.conf.example \
        /etc/nginx/sites-available/megalog
sudo ln -sf /etc/nginx/sites-available/megalog /etc/nginx/sites-enabled/megalog

sudo nginx -t && sudo systemctl reload nginx
```

### 4.9. Habilitar e iniciar

```bash
sudo systemctl enable --now megalog-receiver
sudo systemctl enable --now megalog-processor
sudo systemctl enable --now megalog-web
sudo systemctl enable --now megalog-archive.timer
sudo systemctl enable --now megalog-analyze.timer
sudo systemctl enable --now megalog-retention.timer
```

---

## 5. Configurar o Mikrotik

No terminal RouterOS:

```routeros
# 1. Criar a action de log remoto
/system logging action
add name=megalog \
    target=remote \
    remote=10.100.100.2 \
    remote-port=514 \
    bsd-syslog=yes \
    syslog-facility=daemon \
    syslog-severity=info

# 2. Direcionar o tópico firewall para o servidor
/system logging
add action=megalog topics=firewall

# 3. Garantir que as regras NAT estão com log=yes
/ip firewall nat
add chain=srcnat action=masquerade \
    out-interface=ether1-wan \
    log=yes log-prefix="CGNAT:"
```

Substituir `10.100.100.2` pelo IP do servidor MegaLog.

### Confirmar recebimento

No servidor:

```bash
# 1. Tcpdump confirma pacotes UDP chegando
sudo tcpdump -i any udp port 514 -n -c 20

# 2. Receiver tá gravando?
sudo journalctl -u megalog-receiver -f

# 3. Arquivo .raw está crescendo?
watch -n 2 'ls -lh /dados1/stream/'

# 4. Processor inserindo?
sudo journalctl -u megalog-processor -f
```

---

## 6. Verificação pós-instalação

### Serviços ativos

```bash
sudo systemctl is-active megalog-receiver megalog-processor megalog-web nginx
# Esperado: active × 4

sudo systemctl list-timers megalog-*
# Esperado: 3 timers agendados (archive 02:00, analyze 02:30, retention seg 03:00)
```

### Portas escutando

```bash
sudo ss -tlnp | grep -E ':(80|5000)\b'
# Esperado: nginx em 80 (todas interfaces), uvicorn em 5000

sudo ss -ulnp | grep ':514'
# Esperado: python (receiver) em 514
```

### Smoke test API

```bash
curl -s http://127.0.0.1/api/healthz
# Esperado: {"status":"ok"}

curl -s -c /tmp/c.txt -X POST -H "Content-Type: application/json" \
     -d '{"username":"admin","password":"megalog123"}' \
     http://127.0.0.1/api/auth/login
# Esperado: {"user_id":1,"username":"admin","role":"admin"}

curl -s -b /tmp/c.txt http://127.0.0.1/api/system-status | head -20
# Esperado: JSON com cpu/ram/disks/services/ingest
```

### UI no browser

Acesse `http://<IP-do-servidor>/`. Login admin / megalog123. Dashboard deve mostrar:
- 4 cards de hardware com valores reais
- 4 cards de ingestão (buffer .raw cresce conforme o Mikrotik envia)
- "Receiver UDP: ACTIVE" em verde
- "Processador: ACTIVE" em verde
- Calendário 14 dias (vazio no início; popula após primeiro `archive`)

### Trocar senha admin (obrigatório)

Sidebar → "Trocar senha". Mínimo 8 caracteres.

---

## 7. Atualização (upgrade do código)

```bash
# 1. Parar serviços
sudo systemctl stop megalog-receiver megalog-processor megalog-web

# 2. Atualizar código fonte
cd /usr/local/src/megalog-v5
sudo git pull              # ou rsync do novo código

# 3. Atualizar deps Python (se mudaram)
sudo .venv/bin/pip install -e .

# 4. Rebuild frontend (se mudou)
cd frontend
sudo npm install
sudo npm run build

# 5. Recarregar systemd (se units mudaram)
sudo systemctl daemon-reload

# 6. Reiniciar
sudo systemctl start megalog-receiver megalog-processor megalog-web
sudo systemctl reload nginx

# 7. Verificar
sudo systemctl is-active megalog-{receiver,processor,web} nginx
```

Os dados em `/dados1/hot/`, `/dados2/cold/`, `/dados1/state/` são preservados.

---

## 8. Desinstalação

### Preservando dados (default)

```bash
sudo /usr/local/src/megalog-v5/deploy/uninstall.sh
```

Para serviços, desabilita units, remove unit files, remove site nginx.
**Mantém:** `/dados1/`, `/dados2/`, `/etc/megalog/`, usuário `megalog`.

### Apagando TUDO

```bash
sudo /usr/local/src/megalog-v5/deploy/uninstall.sh --purge
```

Confirma interativamente antes de apagar dados. Remove diretórios + config + usuário sistema.

---

## Próximo passo

Veja [docs/OPERATIONS.md](OPERATIONS.md) para rotinas operacionais (jobs, backup, monitoria) ou [docs/MANUAL.md](MANUAL.md) para o manual narrativo do operador.
