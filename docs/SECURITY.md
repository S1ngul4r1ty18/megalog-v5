# Segurança do MegaLog v5

> Modelo de ameaças, controles atuais, hardening recomendado e procedimentos
> de resposta.

## Índice

1. [Modelo de ameaças](#1-modelo-de-ameaças)
2. [Controles atuais](#2-controles-atuais)
3. [Hardening recomendado](#3-hardening-recomendado)
4. [Rotação de chaves e credenciais](#4-rotação-de-chaves-e-credenciais)
5. [Resposta a incidentes](#5-resposta-a-incidentes)
6. [Compliance](#6-compliance)

---

## 1. Modelo de ameaças

### Premissas

- Sistema é **LAN-only** (confirmado pelo usuário). Não exposto à Internet.
- Usuários são técnicos da operadora, com login pessoal.
- Mikrotiks são roteadores próprios da operadora, na mesma LAN.
- Há audit trail legal (Marco Civil): logs **não podem** ser apagados arbitrariamente.

### Atores

| Ator | Acesso esperado | Capacidade de ataque |
|---|---|---|
| **Usuário interno (admin/user)** | Web, busca, leitura | Limitada — Pydantic valida tudo |
| **Usuário interno malicioso** | Web | Brute-force admin, exfiltração CSV, abuso de export |
| **Atacante externo na LAN** | Sniffing, port scan, brute-force HTTP | TLS ausente expõe cookies; sem rate limit em login |
| **Atacante via Mikrotik comprometido** | Envia syslog forjado (porta 514) | Pode poluir logs, mas não escala privilege |
| **Atacante com SSH no host** | Já é game-over | Acesso aos arquivos crus → lê histórico de IPs |

### Bens a proteger

1. **Histórico imutável de logs** (compliance Marco Civil) — Parquets em cold
2. **Credenciais de usuários** — `users.password_hash` (Argon2)
3. **SECRET_KEY** — `/etc/megalog/megalog.env` (assina JWTs)
4. **`audit_log`** — registro de quem fez o quê (não-repúdio)

---

## 2. Controles atuais

### Autenticação

| Controle | Implementação |
|---|---|
| Hash de senha | **Argon2id** (lib `argon2-cffi`), parâmetros default (memory=64MB, time=3, parallelism=4) |
| Migração transparente do v4 | Hash legado `hex(salt)$hex(sha256(salt+pass))` aceito; re-hashado para Argon2 no primeiro login |
| Sessão | **JWT HS256** em cookie `HttpOnly; SameSite=Strict; path=/`; TTL configurável (default 60min) |
| Logout | Apaga cookie no browser. JWT continua válido até `exp` (limitação SEC-06) |
| Verificação de senha em tempo constante | `argon2.PasswordHasher.verify` (built-in) + `secrets.compare_digest` no path legado |

### Autorização

| Controle | Implementação |
|---|---|
| `get_current_user` | Decode JWT; falha → 401 |
| `require_admin` | Depende de `get_current_user`; checa `role == "admin"`; falha → 403 |
| Endpoints `/api/admin/*` | Todos com `Depends(require_admin)` |
| Endpoints `/api/{search,analytics,system-status}` | Apenas `get_current_user` (admin OU user) |

### Validação de input

| Controle | Implementação |
|---|---|
| Pydantic v2 | Todos os bodies/params validados; tipos errados → 422 |
| `SearchQuery.src_ip` etc | `field_validator` valida IPv4 com `ipaddress.IPv4Address` |
| `SearchQuery.proto` | Validator restringe a `{"TCP", "UDP"}` |
| `SearchQuery.per_page` | `Field(ge=1, le=1000)` |
| Senhas | `Field(min_length=8)` no admin/change-password/create-user |
| Username | `Field(min_length=1, max_length=64)` |
| Roles | `Field(pattern="^(admin|user)$")` |

### SQL Injection

| Controle | Implementação |
|---|---|
| Prepared statements | TODAS as queries com user-input usam `?` ou `$1` (SQLite, DuckDB) |
| f-strings em SQL | Apenas para identificadores hardcoded internos (table names) ou paths controlados |
| Path escape em `read_parquet()` | `safe_path = path.replace("'", "''")` antes de interpolar (DuckDB não aceita `?` em `read_parquet`) |
| Auditoria | Revisão manual de todos os call-sites com input do usuário — nenhum SQL injection real |

### XSS / CSRF

| Controle | Implementação |
|---|---|
| XSS | Vue 3 escapa por default (`{{ }}` é text content); nenhum `v-html` no código |
| CSRF | Cookie `SameSite=Strict` + `HttpOnly` + middleware FastAPI exige `Origin`/`Referer` casando com `Host` em métodos mutadores quando há cookie de sessão |
| CORS | **Não configurado** — FastAPI default rejeita cross-origin (apenas same-origin permitido) |
| Headers de segurança no nginx | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Permissions-Policy`, `Content-Security-Policy` (default-src 'self', img-src também `https:` para badges em docs) |

### Hardening systemd

Aplicado em [todos os 3 services principais](../deploy/systemd/):

```ini
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=...           # apenas o necessário
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 [AF_UNIX]
RestrictNamespaces=true
LockPersonality=true
```

Receiver adicionalmente: `AmbientCapabilities=CAP_NET_BIND_SERVICE` (porta 514 sem root).

### Permissões de arquivo

| Arquivo | Owner | Modo | Por quê |
|---|---|---|---|
| `/etc/megalog/megalog.env` | `root:megalog` | `640` | Contém SECRET_KEY; só root grava, megalog lê |
| `/dados1/state/megalog.db` | `megalog:megalog` | `644` | SQLite ops |
| `/dados1/state/ip_registry.db` | `megalog:megalog` | `644` | SQLite registry |
| `/dados1/{hot,stream}/` | `megalog:megalog` | `755` | Escrita pelo processor/receiver |
| `/dados2/cold/` | `megalog:megalog` | `755` | Escrita pelo archive |

### Audit log

Toda ação sensível registra entry em `audit_log`:

| Action | Quando |
|---|---|
| `login` | Login bem-sucedido |
| `login_failed` | Senha errada ou usuário não existe |
| `logout` | Logout explícito |
| `change_password` | Trocou própria senha |
| `change_password_failed` | Tentou trocar com senha atual errada |
| `search` | Toda busca forense (com payload) |
| `export` | Toda exportação CSV (com payload) |
| `create_user` | Admin criou usuário |
| `deactivate_user` | Admin desativou usuário |
| `update_user_role` | Admin trocou papel |
| `reset_password` | Admin resetou senha de outro |
| `ack_alert` | Admin reconheceu alerta |

Inclui `ip_address` extraído de `request.client.host`. Imutável (não há
endpoint para apagar entries).

---

## 3. Hardening recomendado

### Prioridade 1 — Antes de qualquer exposição além da LAN

#### 3.1 Rate limit no nginx para login

Adicionar em `/etc/nginx/sites-available/megalog`:

```nginx
# No bloco http (em /etc/nginx/nginx.conf), declarar zona
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

# Dentro do server { ... } do megalog
location = /api/auth/login {
    limit_req zone=login burst=3 nodelay;
    proxy_pass http://127.0.0.1:5000;
    # ... resto idêntico ao /api/
}
```

5 req/min/IP em login. Brute-force fica inviável.

#### 3.2 Security headers no nginx

Adicionar no bloco `server`:

```nginx
# Anti-clickjacking
add_header X-Frame-Options "DENY" always;

# Anti-MIME sniffing
add_header X-Content-Type-Options "nosniff" always;

# Política de Referer
add_header Referrer-Policy "same-origin" always;

# Permissions
add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;

# CSP — ajuste conforme necessário (Vue inline styles)
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'" always;
```

`nginx -t && systemctl reload nginx`.

#### 3.3 Bind do uvicorn em 127.0.0.1

```bash
sudo sed -i 's/MEGALOG_WEB_HOST=0.0.0.0/MEGALOG_WEB_HOST=127.0.0.1/' /etc/megalog/megalog.env
sudo systemctl restart megalog-web
```

Não há razão para o uvicorn aceitar conexões além da LAN — nginx faz proxy
local. Reduz superfície.

#### 3.4 Firewall (ufw)

```bash
sudo apt-get install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow from 10.100.100.0/24 to any port 80    # ajuste para sua subnet LAN
sudo ufw allow from 10.100.100.0/24 to any port 514 proto udp
sudo ufw enable
sudo ufw status verbose
```

#### 3.5 Validação fatal da SECRET_KEY no startup

Em `megalog/api/app.py` (lifespan):

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    s: Settings = app.state.settings
    if s.secret_key == "change-me-via-env" or len(s.secret_key) < 32:
        raise RuntimeError(
            "MEGALOG_SECRET_KEY ausente, default ou curta demais. "
            "Gere com: head -c 48 /dev/urandom | base64 | tr -d '\\n'"
        )
    # ... resto
```

Impede startup com chave fraca.

### Prioridade 2 — Próximo sprint

#### 3.6 TLS self-signed (LAN com certificado próprio)

```bash
# Gerar certificado auto-assinado válido por 10 anos
sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/megalog.key \
    -out /etc/nginx/ssl/megalog.crt \
    -subj "/CN=megalog.local"

sudo chmod 600 /etc/nginx/ssl/megalog.key
```

Atualizar nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name _;
    ssl_certificate /etc/nginx/ssl/megalog.crt;
    ssl_certificate_key /etc/nginx/ssl/megalog.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    # ... resto idêntico
}

# Redirect HTTP → HTTPS
server {
    listen 80 default_server;
    return 301 https://$host$request_uri;
}
```

E no env:

```env
MEGALOG_COOKIE_SECURE=true   # nova var, requer mudança no auth.py
```

> ⚠️ Browsers vão mostrar warning de cert não confiável. Para uso em LAN
> controlada, OK. Para produção real, use Let's Encrypt ou cert interno corporativo.

#### 3.7 JWT blacklist (revogação no logout)

Schema novo em `megalog.db`:

```sql
CREATE TABLE jwt_revoked (
    jti       TEXT PRIMARY KEY,
    revoked_at INTEGER,
    expires_at INTEGER         -- limpa após exp
);
CREATE INDEX idx_revoked_expires ON jwt_revoked(expires_at);
```

Em `auth.py:issue_token`, adicionar `jti = secrets.token_urlsafe(8)` ao payload.
Em `auth.py:decode_token`, verificar se `payload["jti"]` está em `jwt_revoked`.
Em `routes/auth.py:logout`, INSERT na tabela.

Job de cleanup: `DELETE FROM jwt_revoked WHERE expires_at < unixepoch()`.

#### 3.8 Hardening systemd avançado

Adicionar em todos os `.service`:

```ini
MemoryDenyWriteExecute=true
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources @debug
ProtectKernelLogs=true
ProtectClock=true
ProtectHostname=true
RemoveIPC=true
```

Testar empiricamente — algum syscall importante do DuckDB pode quebrar.

---

## 4. Rotação de chaves e credenciais

### SECRET_KEY (anual)

Trocar invalida TODAS as sessões existentes (todos relogam).

```bash
NEW=$(head -c 48 /dev/urandom | base64 | tr -d '\n')
sudo sed -i "s/^MEGALOG_SECRET_KEY=.*/MEGALOG_SECRET_KEY=$NEW/" /etc/megalog/megalog.env
sudo systemctl restart megalog-web
```

### Senhas de usuários

Política sugerida:
- Comprimento mínimo: 12 chars (override do default 8)
- Rotação: a cada 90 dias para admin, 180 dias para user
- Não há enforcement automático no MegaLog (manual via `/admin/users → reset password`)

### Certificados TLS (se usados)

Self-signed: validade 10 anos, rotacione se vazar a chave privada.
Let's Encrypt: certbot rotaciona automaticamente.

---

## 5. Resposta a incidentes

### Suspeita de comprometimento de conta

```bash
# 1. Listar logins recentes do usuário
sudo -u megalog sqlite3 /dados1/state/megalog.db <<EOF
.headers on
.mode column
SELECT datetime(ts,'unixepoch','localtime') AS quando, action, ip_address, details
FROM audit_log
WHERE username='suspeita' AND ts > strftime('%s','now','-7 days')
ORDER BY ts DESC;
EOF

# 2. Desativar imediatamente
sudo -u megalog sqlite3 /dados1/state/megalog.db \
  "UPDATE users SET active=0 WHERE username='suspeita'"

# 3. Resetar senha (caso queira reativar depois)
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python -c "
from megalog.api.auth import hash_password
from megalog.storage.operational import OperationalStore
from megalog.config import get_settings
ops = OperationalStore(get_settings().state_dir / 'megalog.db')
u = ops.get_user_by_username('suspeita')
ops.set_user_password(u['id'], hash_password('TROCAR-NO-PROXIMO-LOGIN'))
"

# 4. Forçar troca de SECRET_KEY (invalida TODAS sessões, mata token roubado)
NEW=$(head -c 48 /dev/urandom | base64 | tr -d '\n')
sudo sed -i "s/^MEGALOG_SECRET_KEY=.*/MEGALOG_SECRET_KEY=$NEW/" /etc/megalog/megalog.env
sudo systemctl restart megalog-web
```

### Suspeita de tampering nos logs (Parquets cold)

```bash
# 1. Conferir mtime dos arquivos
ls -la /dados2/cold/

# 2. Comparar com backup
diff <(ls -la /dados2/cold/) <(ls -la /dados2/backups/<data>/cold/)

# 3. Checksums (caso tenha rodado periodicamente)
sha256sum /dados2/cold/*.parquet > /tmp/now.sha256
diff /tmp/now.sha256 /dados2/checksums/<data>.sha256

# 4. Ler audit_log buscando 'export' (única forma legítima de extrair)
sudo -u megalog sqlite3 /dados1/state/megalog.db \
  "SELECT datetime(ts,'unixepoch','localtime'), username, ip_address, details FROM audit_log WHERE action='export' ORDER BY ts DESC LIMIT 50"
```

### Brute-force em login

```bash
# Quantas tentativas falhadas por IP nas últimas 24h
sudo -u megalog sqlite3 /dados1/state/megalog.db <<EOF
SELECT ip_address, COUNT(*) AS tentativas, COUNT(DISTINCT username) AS users_alvo
FROM audit_log
WHERE action='login_failed' AND ts > strftime('%s','now','-1 day')
GROUP BY ip_address
ORDER BY tentativas DESC;
EOF

# Se houver IP suspeito, bloquear no firewall:
sudo ufw deny from <IP-suspeito>
```

---

## 6. Compliance

### Marco Civil da Internet (Lei 12.965/2014)

- **Art. 13:** registros de conexão devem ser guardados por **1 ano**.
- **Art. 14:** registros de aplicação por **6 meses** (não aplicável aqui).
- Disponibilizar mediante ordem judicial.

**Ajuste:** `MEGALOG_DELETE_AFTER_DAYS=365` (default). Não diminuir.

### Anatel Resolução 614/2013

- Provedor SCM deve manter logs CGNAT que permitam identificar usuário a partir
  de IP+porta+timestamp.
- Disponibilizar à autoridade competente em até 10 dias.

**MegaLog atende:** busca via `/api/search` retorna `src_ip` (privado, identifica
o assinante via DHCP/PPP) a partir de `nat_ip + nat_port + ts`.

### Trilha de auditoria

**Imutabilidade:**
- `audit_log` é append-only (não há endpoint DELETE)
- Recomendado backup periódico para storage write-once (S3 com Object Lock, fita)

**Não-repúdio:**
- Cada entry tem `username` + `ip_address` + `ts`
- Login por usuário individual (sem login compartilhado — política operacional)

### Privacidade dos dados

- IPs privados são informações pessoais (LGPD): trate como dado sensível.
- Acesso ao MegaLog deve ser restrito a equipe com necessidade legal.
- Logs de quem fez busca/export ficam em `audit_log` com IP de origem.

---

## Auditoria periódica

Recomendado: a cada 6 meses, rodar:

```bash
cd /usr/local/src/megalog-v5
.venv/bin/python -m pytest tests/ --cov=megalog
.venv/bin/ruff check megalog/
.venv/bin/radon cc megalog/ -nB

# Permissões
ls -la /etc/megalog/megalog.env /dados1/state/

# Audit log: top usuários e ações no período
sudo -u megalog sqlite3 /dados1/state/megalog.db <<EOF
SELECT username, action, COUNT(*)
FROM audit_log
WHERE ts > strftime('%s','now','-180 days')
GROUP BY username, action
ORDER BY 3 DESC LIMIT 30;
EOF

# Rotação de SECRET_KEY (anual)
# Ver seção 4 acima
```

Documente novos achados de segurança no histórico do repositório (commit
message + issue) para que mudanças no modelo de ameaças fiquem rastreáveis.
