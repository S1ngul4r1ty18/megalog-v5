# Referência da API MegaLog v5

> 19 endpoints REST sob `/api/`. OpenAPI interativo em `http://<host>/api/docs`.

## Convenções

- **Base URL:** `http://<host>/api/` (sem `/api/v1/` — versionamento será adicionado se necessário)
- **Auth:** cookie `megalog_session` (JWT HS256, HttpOnly, SameSite=Strict). Login via `/api/auth/login`.
- **Content-Type:** request `application/json`, response `application/json` (exceto `/api/search/export` que retorna `text/csv`)
- **Erros:** `{"detail": "mensagem"}` com código HTTP apropriado
- **Códigos de papel:** `admin` (todos endpoints) ou `user` (sem `/api/admin/*`)

## Sumário dos endpoints

| Método | Path | Auth | Descrição |
|---|---|---|---|
| `POST` | `/api/auth/login` | público | Autenticar; seta cookie |
| `POST` | `/api/auth/logout` | user | Invalidar cookie |
| `GET` | `/api/auth/me` | user | Retornar usuário atual |
| `POST` | `/api/auth/change-password` | user | Trocar senha própria |
| `POST` | `/api/search` | user | Busca forense paginada |
| `POST` | `/api/search/export` | user | Export CSV streaming |
| `GET` | `/api/analytics/daily` | user | Lista de dias com volume |
| `GET` | `/api/analytics/top-ips` | user | Top N IPs do registry global |
| `GET` | `/api/analytics/calendar` | user | Map date → {count, alert} |
| `GET` | `/api/system-status` | user | CPU/RAM/disco/serviços/ingestão |
| `GET` | `/api/healthz` | público | Healthcheck |
| `GET` | `/api/admin/users` | admin | Listar usuários |
| `POST` | `/api/admin/users` | admin | Criar usuário |
| `DELETE` | `/api/admin/users/{user_id}` | admin | Desativar usuário |
| `PUT` | `/api/admin/users/{user_id}/role` | admin | Alterar papel |
| `POST` | `/api/admin/users/{user_id}/reset-password` | admin | Resetar senha |
| `GET` | `/api/admin/audit` | admin | Listar audit_log |
| `GET` | `/api/admin/alerts` | admin | Listar alertas de anomalia |
| `GET` | `/api/admin/alerts/{alert_id}` | admin | Detalhe de um alerta |
| `POST` | `/api/admin/alerts/{alert_id}/ack` | admin | Reconhecer alerta |
| `GET` | `/api/openapi.json` | público | Schema OpenAPI |
| `GET` | `/api/docs` | público | Swagger UI |

---

## Autenticação

### `POST /api/auth/login`

```bash
curl -c /tmp/c.txt -X POST -H "Content-Type: application/json" \
     -d '{"username":"admin","password":"megalog123"}' \
     http://127.0.0.1/api/auth/login
```

**Request:**
```json
{ "username": "admin", "password": "megalog123" }
```

**Response 200:**
```json
{ "user_id": 1, "username": "admin", "role": "admin" }
```
Cookie `megalog_session` setado (HttpOnly, SameSite=Strict, expira em
`session_timeout_minutes` — default 60).

**Response 401:**
```json
{ "detail": "Credenciais inválidas" }
```

### `POST /api/auth/logout`

Invalida o cookie no browser **e** revoga o `jti` do JWT na tabela
`revoked_jti` (válida até o `exp` original). Reuso do cookie pós-logout
retorna 401 "Sessão revogada".

**Response 200:** `{"ok": true}`

### `GET /api/auth/me`

```bash
curl -b /tmp/c.txt http://127.0.0.1/api/auth/me
```

**Response 200:**
```json
{ "user_id": 1, "username": "admin", "role": "admin" }
```

**Response 401** (sem cookie ou expirado): `{"detail": "Sessão ausente"}` ou `{"detail": "Sessão inválida ou expirada"}`

### `POST /api/auth/change-password`

```bash
curl -b /tmp/c.txt -X POST -H "Content-Type: application/json" \
     -d '{"current_password":"velha","new_password":"nova-senha-12345"}' \
     http://127.0.0.1/api/auth/change-password
```

**Validações:** `new_password` mínimo 8 caracteres.

**Response 200:** `{"ok": true}`
**Response 403:** `{"detail": "Senha atual incorreta"}`

---

## Busca forense

### `POST /api/search`

Filtra logs em uma data, retornando paginado.

```bash
curl -b /tmp/c.txt -X POST -H "Content-Type: application/json" \
     -d '{"date":"2026-04-23","nat_ip":"170.245.175.121","page":1,"per_page":50}' \
     http://127.0.0.1/api/search
```

**SearchQuery (todos opcionais exceto `date`):**
```typescript
{
  date:     string;       // "YYYY-MM-DD" obrigatório
  src_ip?:  string;       // IPv4 (privado, ex: "100.80.0.119")
  dst_ip?:  string;       // IPv4
  nat_ip?:  string;       // IPv4 (público pós-CGNAT)
  src_port?: number;      // 0-65535
  dst_port?: number;
  nat_port?: number;
  proto?:   "TCP" | "UDP";
  ts_start?: number;      // UNIX epoch (segundos)
  ts_end?:   number;
  page?:    number;       // 1+ (default 1)
  per_page?: number;      // 1-1000 (default 100)
}
```

**Response 200:**
```json
{
  "total": 95934,
  "page": 1,
  "per_page": 50,
  "rows": [
    {
      "ts": 1714003200,
      "ts_iso": "2026-04-23T00:00:00",
      "src_ip": "100.80.0.15",
      "src_port": 37944,
      "dst_ip": "34.233.48.221",
      "dst_port": 5222,
      "nat_ip": "170.245.175.121",
      "nat_port": 37944,
      "proto": "TCP",
      "in_iface": "VLAN160-PE01",
      "out_iface": "ether2",
      "conn_state": "new",
      "has_snat": true,
      "tcp_flags": null,
      "pkt_len": 60
    }
  ]
}
```

**Response 404** (sem partição para a data): `{"detail": "Nenhuma partição para a data 2099-01-01"}`

**Response 422** (validação Pydantic): IP/proto/porta inválido.

### `POST /api/search/export`

Mesmo body do `/api/search`. Retorna **CSV streaming** (até 100k linhas).

```bash
curl -b /tmp/c.txt -X POST -H "Content-Type: application/json" \
     -d '{"date":"2026-04-23","nat_ip":"170.245.175.121"}' \
     -o megalog-2026-04-23.csv \
     http://127.0.0.1/api/search/export
```

**Headers de response:**
```
Content-Type: text/csv
Content-Disposition: attachment; filename="megalog-2026-04-23.csv"
```

**Formato:**
```csv
ts_iso,ts,src_ip,src_port,dst_ip,dst_port,nat_ip,nat_port,proto,in_iface,out_iface,conn_state,has_snat,tcp_flags,pkt_len
2026-04-23T00:00:00,1714003200,100.80.0.15,37944,34.233.48.221,5222,170.245.175.121,37944,TCP,VLAN160-PE01,ether2,new,True,,60
...
```

---

## Analytics

### `GET /api/analytics/daily`

Lista de todos os dias com partição (hot ou cold) + contagem cacheada.

```bash
curl -b /tmp/c.txt http://127.0.0.1/api/analytics/daily
```

**Response 200:**
```json
[
  {
    "date": "2026-04-20",
    "log_count": 2741707,
    "db_size_bytes": 24529521,
    "kind": "cold",
    "format": "parquet",
    "alert": false
  },
  {
    "date": "2026-04-27",
    "log_count": 62915,
    "db_size_bytes": 4730880,
    "kind": "hot",
    "format": "duckdb",
    "alert": false
  }
]
```

`kind` ∈ `{"hot", "cold", "absent"}`. `alert: true` se há `anomaly_alerts` para a data.

### `GET /api/analytics/top-ips?limit=N`

Top IPs do registry GLOBAL (cross-day). `limit` default 50, max 1000.

```bash
curl -b /tmp/c.txt 'http://127.0.0.1/api/analytics/top-ips?limit=10'
```

**Response 200:**
```json
[
  {
    "ip_id": 5,
    "ip": "192.168.20.105",
    "total_hits": 43905,
    "first_seen": 1714050000,
    "last_seen":  1714076400,
    "is_hot": false
  }
]
```

`first_seen`/`last_seen` são UNIX timestamps (segundos).

### `GET /api/analytics/calendar`

Map compacto `date → {count, alert}` para badge no dashboard.

**Response 200:**
```json
{
  "2026-04-20": { "log_count": 2741707, "alert": false },
  "2026-04-23": { "log_count": 2651065, "alert": false },
  "2026-04-27": { "log_count": 62915,   "alert": false }
}
```

---

## Sistema

### `GET /api/system-status`

CPU, RAM, swap, discos, serviços systemd e métricas de ingestão atuais.

**Response 200:**
```json
{
  "cpu": {
    "per_core_pct": [12.5, 8.3, 15.0, 9.1],
    "avg_pct": 11.2,
    "load_avg": [0.53, 0.77, 0.60]
  },
  "ram": {
    "total_bytes":     16435896320,
    "used_bytes":      5236789248,
    "available_bytes": 11199107072,
    "used_pct": 31.7
  },
  "swap": { "total_bytes": 0, "used_bytes": 0, "used_pct": 0 },
  "disks": {
    "hot":  { "path": "/dados1/hot",  "total_bytes": ..., "used_bytes": ..., "free_bytes": ..., "used_pct": 0.1 },
    "cold": { "path": "/dados2/cold", ... },
    "state": { "path": "/dados1/state", ... },
    "stream": { "path": "/dados1/stream", ... }
  },
  "services": {
    "receiver":  "active",
    "processor": "active",
    "web":       "active"
  },
  "ingest": {
    "raw_buffer_bytes": 16679125,
    "raw_last_seen":    "2026-04-27T17:18:13",
    "today_date":       "2026-04-27",
    "today_db_path":    "/dados1/hot/2026-04-27.duckdb",
    "today_db_size":    7090176,
    "today_log_count":  125650,
    "today_count_age_seconds": 2.3
  },
  "now": "2026-04-27T17:18:15"
}
```

`today_count_age_seconds` indica idade do cache do `today_log_count`. UI mostra
"· há Xs" quando >5s.

`services.<name>` ∈ `{"active", "inactive", "failed", "activating", "unknown"}`.

### `GET /api/healthz`

Healthcheck público (sem auth). Use em load balancer / monitoria externa.

**Response 200:** `{"status": "ok", "ingest": {...}}` quando saudável.

**Response 503:** `{"status": "degraded", "issues": [...]}` se algum sinal
crítico falhar — disco hot/cold > 95%, disco state > 90%, qualquer serviço
não-ativo (receiver/processor/web), ingestão sem packets > 5min, ou
`ip_registry.db` > 5 GB. Útil para monitoramento externo (cron + alerta).

---

## Admin (require_admin)

### `GET /api/admin/users`

```bash
curl -b /tmp/c.txt http://127.0.0.1/api/admin/users
```

**Response 200:**
```json
[
  {
    "id": 1,
    "username": "admin",
    "role": "admin",
    "created_at": 1777318676,
    "last_login": 1714076400,
    "active": 1
  }
]
```

### `POST /api/admin/users`

```bash
curl -b /tmp/c.txt -X POST -H "Content-Type: application/json" \
     -d '{"username":"bob","password":"bob-pwd-12345","role":"user"}' \
     http://127.0.0.1/api/admin/users
```

**Validações:** `username` 1-64 chars; `password` 8-256 chars; `role` ∈ `{user, admin}`.

**Response 201:** `{"user_id": 2}`
**Response 409** (duplicate): `{"detail": "Usuário já existe"}`

### `DELETE /api/admin/users/{user_id}`

Desativa (não apaga) o usuário.

**Response 200:** `{"ok": true}`
**Response 400:** `{"detail": "Não pode desativar a si mesmo"}`
**Response 404:** `{"detail": "Usuário não encontrado"}`

### `PUT /api/admin/users/{user_id}/role`

```bash
curl -b /tmp/c.txt -X PUT -H "Content-Type: application/json" \
     -d '{"role":"admin"}' \
     http://127.0.0.1/api/admin/users/2/role
```

### `POST /api/admin/users/{user_id}/reset-password`

```bash
curl -b /tmp/c.txt -X POST -H "Content-Type: application/json" \
     -d '{"new_password":"reset-pwd-12345"}' \
     http://127.0.0.1/api/admin/users/2/reset-password
```

### `GET /api/admin/audit`

```bash
curl -b /tmp/c.txt 'http://127.0.0.1/api/admin/audit?limit=20&action=login'
```

**Query params:**
- `limit` (default 200)
- `offset` (default 0)
- `user_id` (filtra por usuário)
- `action` (filtra por ação: login, logout, login_failed, search, export, change_password, change_password_failed, create_user, deactivate_user, update_user_role, reset_password, ack_alert)

**Response 200:**
```json
[
  {
    "id": 42,
    "user_id": 1,
    "username": "admin",
    "action": "login",
    "details": null,
    "ip_address": "10.100.100.5",
    "ts": 1714076400
  }
]
```

### `GET /api/admin/alerts?only_unack=true`

```bash
curl -b /tmp/c.txt http://127.0.0.1/api/admin/alerts
```

**Response 200:**
```json
[
  {
    "id": 1,
    "date": "2026-04-23",
    "log_count": 2651065,
    "db_size_bytes": 23952451,
    "expected_count": 800000,
    "ratio": 3.31,
    "analysis": "Volume 3.3x acima da média ...",
    "details_json": "{...stats...}",
    "classification": "Flood/Loop/Tráfego Automatizado",
    "created_at": 1714060800,
    "acknowledged": 0,
    "acknowledged_by": null,
    "acknowledged_at": null
  }
]
```

`details_json` é uma string JSON com a estrutura completa de stats (top_ips,
top_dst_ports, top_dst_ips, port_ranges, protocols, ntp_detail, dns_detail,
top_talker, flow_repetition, protocol_anomaly, classification, classification_score,
classification_reasons, possible_causes).

### `POST /api/admin/alerts/{alert_id}/ack`

Marca alerta como reconhecido.

**Response 200:** `{"ok": true}`
**Response 404:** `{"detail": "Alerta inexistente ou já reconhecido"}`

---

## Códigos de erro globais

| Código | Quando |
|---|---|
| 200 | OK |
| 201 | Criado (POST /api/admin/users) |
| 400 | Operação inválida (ex: desativar a si mesmo) |
| 401 | Não autenticado / sessão expirada |
| 403 | Sem permissão (não-admin tentando admin) ou senha atual incorreta |
| 404 | Recurso não encontrado |
| 409 | Conflito (ex: usuário duplicado) |
| 422 | Validação Pydantic falhou (input mal formado) |
| 500 | Erro interno (ver journalctl -u megalog-web) |

---

## Limites

| Limite | Valor |
|---|---|
| Search per_page max | 1000 |
| Export CSV max rows | 100k (hardcoded) |
| Body request | 8 MB (nginx `client_max_body_size`) |
| Read/Write timeout | 120s (nginx) |
| Sessão TTL | 60 min (config: `session_timeout_minutes`) |
| Senha mínima | 8 caracteres |
| Username 1-64 chars | (Pydantic) |

---

## Exemplos de scripts

### Script Python para extrair logs do dia

```python
import httpx
from datetime import date

c = httpx.Client(base_url="http://127.0.0.1", timeout=30.0)
c.post("/api/auth/login", json={"username":"admin","password":"senha"})

# Pega tudo do dia
all_rows = []
page = 1
while True:
    r = c.post("/api/search", json={
        "date": date.today().isoformat(),
        "page": page, "per_page": 1000,
    }).json()
    all_rows.extend(r["rows"])
    if page * r["per_page"] >= r["total"]:
        break
    page += 1

print(f"Coletados {len(all_rows)} logs")
```

### Curl para buscar IP origem específico

```bash
curl -b /tmp/c.txt -X POST \
     -H "Content-Type: application/json" \
     -d '{"date":"2026-04-23","src_ip":"100.80.0.119","per_page":500}' \
     http://127.0.0.1/api/search | jq '.total, (.rows[0])'
```

### Cron job que envia alertas pendentes por email

```bash
#!/bin/bash
ALERTS=$(curl -fs -b /tmp/c.txt 'http://127.0.0.1/api/admin/alerts?only_unack=true' | jq length)
if [ "$ALERTS" -gt 0 ]; then
    echo "$ALERTS alertas de anomalia pendentes" | mail -s "MegaLog" admin@example.com
fi
```

---

## OpenAPI Schema

JSON completo em `http://<host>/api/openapi.json`. Importável em Postman, Insomnia, Bruno, ou para gerar SDK clients.

UI interativa em `http://<host>/api/docs` (Swagger).
