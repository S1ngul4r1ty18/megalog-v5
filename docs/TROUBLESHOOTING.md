# Troubleshooting do MegaLog v5

> Receitas para problemas comuns. Use Ctrl+F para localizar pelo sintoma.

## Índice

- [Receiver não recebe pacotes do Mikrotik](#receiver-não-recebe-pacotes-do-mikrotik)
- [Processor parou de inserir](#processor-parou-de-inserir)
- [Web retorna 500 / "Conflicting lock"](#web-retorna-500--conflicting-lock)
- [Dashboard mostra "—" em "Logs hoje"](#dashboard-mostra--em-logs-hoje)
- [Login falha mesmo com senha correta](#login-falha-mesmo-com-senha-correta)
- [DuckDB hot corrompido](#duckdb-hot-corrompido)
- [Disco cheio](#disco-cheio)
- [Stream `.raw` cresceu demais](#stream-raw-cresceu-demais)
- [nginx -t falha (`duplicate default server`)](#nginx--t-falha-duplicate-default-server)
- [systemctl mostra "activating (auto-restart)"](#systemctl-mostra-activating-auto-restart)
- [Import legacy falha com "readonly database"](#import-legacy-falha-com-readonly-database)
- [Import legacy falha com "Can't find home directory"](#import-legacy-falha-com-cant-find-home-directory)
- [Frontend mostra página em branco](#frontend-mostra-página-em-branco)
- [Build do frontend falha](#build-do-frontend-falha)

---

## Receiver não recebe pacotes do Mikrotik

### Sintomas
- `journalctl -u megalog-receiver -f` não mostra "Stats: N pkt/s"
- `ls -la /dados1/stream/` não tem arquivo da hora atual ou não cresce
- Dashboard "Buffer .raw" mostra 0 ou tamanho parado

### Diagnóstico

```bash
# 1. O serviço está rodando?
systemctl is-active megalog-receiver
# Esperado: active

# 2. A porta está bound?
sudo ss -ulnp | grep ':514'
# Esperado: 0.0.0.0:514 users:(("python",pid=...))

# 3. Pacotes UDP estão chegando ao host?
sudo tcpdump -i any udp port 514 -n -c 20
# Se nada aparece → problema de rede/firewall

# 4. Mikrotik está configurado e ativo?
# No RouterOS:
/system logging action print
/system logging print
# Confirmar que existe action 'megalog' e topic 'firewall' apontando pra ela

# 5. Mikrotik pinga o servidor?
# /ping <IP-do-servidor>
```

### Causas comuns

**(a) Firewall bloqueando UDP 514**
```bash
sudo ufw status              # se ufw instalado
sudo iptables -L INPUT -n    # se iptables direto
# Liberar:
sudo ufw allow 514/udp
```

**(b) Roteador não está logando NAT**
No RouterOS: `/ip firewall nat` — todas as regras com `log=yes`?

**(c) Receiver bound em interface errada**
Verifique `MEGALOG_RECEIVER_HOST` em `/etc/megalog/megalog.env`. Se restringiu
a um IP específico, pacotes vindos de outra interface não chegam.

**(d) Permissão CAP_NET_BIND_SERVICE**
```bash
# Na unit file:
grep AmbientCapabilities /etc/systemd/system/megalog-receiver.service
# Esperado: AmbientCapabilities=CAP_NET_BIND_SERVICE
# Se não, reinstale via install.sh ou edite manualmente
```

---

## Processor parou de inserir

### Sintomas
- Buffer `.raw` cresce indefinidamente (arquivo `/dados1/stream/HH.raw` muito grande)
- Dashboard "Logs hoje" não cresce
- DuckDB hot do dia (`/dados1/hot/$(date +%F).duckdb`) não atualiza tamanho

### Diagnóstico

```bash
# 1. Serviço está rodando?
systemctl is-active megalog-processor

# 2. Processor está crashando?
journalctl -u megalog-processor --since '15 minutes ago' --no-pager

# 3. Offset está avançando?
cat /dados1/state/stream_offsets.json
# Esperado: {"2026-04-27-17.raw": <número-grande>}
# Se o número é o mesmo da última verificação, processor está travado
```

### Causas e correções

**(a) Crash do processor**
- Ver stack trace no journalctl
- `systemctl restart megalog-processor`

**(b) Disco hot cheio**
```bash
df -h /dados1
# Se >95%, libere espaço (apague .raw antigos em .processed/, force archive)
```

**(c) Lock conflict crônico (raro)**
- Mata o processor: `systemctl kill megalog-processor`
- Espera 5s
- `systemctl start megalog-processor`

**(d) Parser falhando em todas as linhas (raro, indica regex quebrado)**
```bash
# Conte erros
journalctl -u megalog-processor --since '1 hour ago' --no-pager | grep -c errors
# Se ~100% das linhas dão erro, regex pode estar incompatível com formato novo do Mikrotik
# Capture algumas linhas e teste manualmente:
head -3 /dados1/stream/$(date +%F-%H).raw
```

---

## Web retorna 500 / "Conflicting lock"

### Sintoma
- `curl /api/search` ou busca via UI retorna `{"detail":"Internal Server Error"}`
- `journalctl -u megalog-web` mostra `_duckdb.IOException: Could not set lock`

### Causa
Tentativa de leitura no DuckDB hot que o processor mantém em write-lock.

### Comportamento esperado
`open_for_query` faz **retry linear** (8 × 0.5s = 4s) cobrindo a janela de
close do processor (`duckdb_close_interval_seconds=3.0`). Em condições normais,
busca passa.

### Solução se persiste

```bash
# 1. Verifique o close interval
grep DUCKDB_CLOSE /etc/megalog/megalog.env
# Default: MEGALOG_DUCKDB_CLOSE_INTERVAL_SECONDS=3.0
# Se está alto (ex: 10), diminua:
sudo sed -i 's/^MEGALOG_DUCKDB_CLOSE_INTERVAL_SECONDS=.*/MEGALOG_DUCKDB_CLOSE_INTERVAL_SECONDS=3.0/' /etc/megalog/megalog.env
sudo systemctl restart megalog-processor

# 2. Se múltiplos clientes web concorrentes, considere:
# - Aumentar retries em open_for_query (megalog/storage/partitions.py)
# - Implementar cache no FastAPI (PER-01 da auditoria)
```

---

## Dashboard mostra "—" em "Logs hoje"

### Sintoma
- Card "LOGS HOJE" no dashboard mostra "—" em vez do número, ou pisca

### Diagnóstico atual
Já mitigado em v5.0.0a1 com cache TTL=8s + defesa client-side (mantém último valor).
Se ainda acontece:

```bash
# 1. /api/system-status retorna o campo?
curl -s -b /tmp/cookies.txt http://127.0.0.1/api/system-status | jq '.ingest'
# Esperado: today_log_count = número (não null)

# 2. Se today_log_count = null persistente:
#    - Processor segura lock por mais que 4s. Diminua o close interval:
sudo sed -i 's/MEGALOG_DUCKDB_CLOSE_INTERVAL_SECONDS=.*/MEGALOG_DUCKDB_CLOSE_INTERVAL_SECONDS=2.0/' /etc/megalog/megalog.env
sudo systemctl restart megalog-processor megalog-web
```

---

## Login falha mesmo com senha correta

### Sintomas
- POST `/api/auth/login` retorna 401 com senha que você jura estar certa
- audit_log mostra "login_failed" do seu usuário

### Causas

**(a) Senha foi resetada por outro admin**
```bash
# Veja audit recente
sudo -u megalog sqlite3 /dados1/state/megalog.db \
  "SELECT datetime(ts,'unixepoch'), action, username, details, ip_address FROM audit_log ORDER BY ts DESC LIMIT 20"
```

**(b) Usuário foi desativado**
```bash
sudo -u megalog sqlite3 /dados1/state/megalog.db \
  "SELECT id, username, active FROM users WHERE username='seu-user'"
# Se active=0, reative:
sudo -u megalog sqlite3 /dados1/state/megalog.db \
  "UPDATE users SET active=1 WHERE username='seu-user'"
```

**(c) SECRET_KEY mudou (mas isso só invalida sessões existentes, não bloqueia login)**

**(d) Esqueci a senha do admin único**
```bash
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python -c "
from megalog.config import get_settings
from megalog.storage.operational import OperationalStore
from megalog.api.auth import hash_password
ops = OperationalStore(get_settings().state_dir / 'megalog.db')
u = ops.get_user_by_username('admin')
ops.set_user_password(u['id'], hash_password('nova-senha-segura'))
print('OK')
"
```

---

## DuckDB hot corrompido

### Sintomas
- `journalctl -u megalog-processor` mostra `Catalog Error: Table 'logs' does not exist`
- Ou `_duckdb.IOException: Failure to open file ... not a database file`
- DuckDB hot do dia não abre nem read-only

### Diagnóstico

```bash
TODAY=/dados1/hot/$(date +%F).duckdb
ls -la "$TODAY"*    # checa .duckdb e .duckdb.wal
file "$TODAY"
```

### Recovery

```bash
# 1. Para o processor para liberar o arquivo
sudo systemctl stop megalog-processor

# 2. Backup do corrompido (caso queira investigar)
sudo cp "$TODAY" "$TODAY.corrupted"

# 3. Tenta recovery via DuckDB
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python -c "
import duckdb
import shutil
src = '/dados1/hot/$(date +%F).duckdb'
con = duckdb.connect(src)
con.execute('CHECKPOINT')
# Se passou, está OK
con.execute('SELECT COUNT(*) FROM logs')
con.close()
print('recuperado')
"

# 4. Se não recuperou, restaura do .raw (re-processar)
sudo systemctl stop megalog-processor
sudo rm "$TODAY" "$TODAY.wal"

# Resetar offset para reprocessar do começo do .raw atual
TODAY_HOUR=/dados1/stream/$(date +%F-%H).raw
sudo -u megalog python3 -c "
import json
p = '/dados1/state/stream_offsets.json'
d = json.load(open(p))
d.pop('$(basename $TODAY_HOUR)', None)
json.dump(d, open(p, 'w'))
"

sudo systemctl start megalog-processor
# Vai reprocessar do offset 0 → recria DuckDB hot
```

> ⚠️ Logs do .raw que já tinham sido processados podem ser RE-inseridos
> (duplicação). Para evitar, limpe o DuckDB primeiro: `rm /dados1/hot/$(date +%F).duckdb`.

---

## Disco cheio

### Diagnóstico

```bash
df -h /dados1 /dados2
du -sh /dados1/* /dados2/* 2>/dev/null
```

### Soluções por área

**Hot cheio (`/dados1/hot/`)**
```bash
# Forçar archive de tudo > 7 dias (em vez do default 30)
sudo MEGALOG_HOT_RETENTION_DAYS=7 systemctl start megalog-archive.service

# Ou direto no env (permanente):
sudo sed -i 's/MEGALOG_HOT_RETENTION_DAYS=.*/MEGALOG_HOT_RETENTION_DAYS=7/' /etc/megalog/megalog.env
```

**Stream cheio (`/dados1/stream/`)**
- `.raw` ativos crescem se processor não está consumindo
- Veja [Processor parou de inserir](#processor-parou-de-inserir)
- `.processed/` é apagado automaticamente após 24h, mas se o filesystem não suporta
  mtime, ajuste manualmente: `sudo find /dados1/stream/.processed -type f -mtime +1 -delete`

**Cold cheio (`/dados2/cold/`)**
- Aplica retenção mais agressiva:
```bash
sudo sed -i 's/MEGALOG_DELETE_AFTER_DAYS=.*/MEGALOG_DELETE_AFTER_DAYS=180/' /etc/megalog/megalog.env
sudo systemctl start megalog-retention.service
```
> ⚠️ Compliance Marco Civil exige 365 dias mínimo. Não diminua sem consultar jurídico.

**State cheio (`/dados1/state/`)**
- Improvável (registry+ops costuma ser <100MB)
- Verifique se há `-wal` órfão (processor crashou): `ls -la /dados1/state/`
- WAL grande (>500MB) → checkpoint forçado:
```bash
sudo systemctl stop megalog-processor
sudo -u megalog sqlite3 /dados1/state/ip_registry.db "PRAGMA wal_checkpoint(TRUNCATE)"
sudo systemctl start megalog-processor
```

---

## Stream `.raw` cresceu demais

Veja [Processor parou de inserir](#processor-parou-de-inserir). Se o
`.raw` da hora atual passa de centenas de MB, é sinal claro de processor parado.

Limpeza emergencial (perde dados):
```bash
# Última opção: drop total dos .raw não processados
sudo systemctl stop megalog-receiver megalog-processor
sudo rm -f /dados1/stream/*.raw
sudo rm -f /dados1/state/stream_offsets.json
sudo systemctl start megalog-receiver megalog-processor
```

---

## nginx -t falha (`duplicate default server`)

### Sintoma

```
nginx: [emerg] a duplicate default server for 0.0.0.0:80 in /etc/nginx/sites-enabled/megalog:12
```

### Causa
Debian instala um site `default` em `/etc/nginx/sites-enabled/default` que
também declara `default_server`.

### Solução

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

`install.sh` já faz isso automaticamente em novas instalações.

---

## systemctl mostra "activating (auto-restart)"

Significa que o serviço está crashando e o systemd restartando em loop.

### Diagnóstico

```bash
# Status detalhado
systemctl status megalog-web --no-pager -l

# Stack trace
journalctl -u megalog-web -n 50 --no-pager
```

### Causas frequentes

**(a) Lock conflict no startup (web tenta abrir registry que processor tem em write)**
- Resolvido em v5.0.0a1 (web abre em RO). Se acontece, pode ser bug.

**(b) SECRET_KEY ausente ou inválida**
```bash
grep SECRET_KEY /etc/megalog/megalog.env
# Deve ter valor não-vazio com 32+ chars
```

**(c) Diretório não existe ou sem permissão**
```bash
ls -la /dados1/state /dados1/hot /dados2/cold
# Owner deve ser megalog:megalog
```

**(d) Porta já em uso**
```bash
sudo ss -tlnp | grep ':5000'
# Deve mostrar só o megalog-web
```

---

## Import legacy falha com "readonly database"

### Sintoma
```
sqlite3.OperationalError: attempt to write a readonly database
```

### Causa
DB origem em diretório read-only para o usuário `megalog`. SQLite tenta criar
WAL/SHM e falha.

### Mitigação
Já aplicada na v5.0.0a1: `import_legacy` sempre copia para tmpdir gravável.
Se persiste, verifique:

```bash
# /tmp tem espaço?
df -h /tmp

# Permissões do tmp do user megalog?
sudo -u megalog mktemp -d
# Esperado: /tmp/tmp.XXXXXXX criado com sucesso
```

---

## Import legacy falha com "Can't find home directory"

### Sintoma
```
IO Error: Can't find the home directory at '/home/megalog'
```

### Causa
DuckDB precisa de `home_directory` para cache de extensões (`INSTALL sqlite`).
Usuário `megalog` é sistema sem `/home`.

### Mitigação
Já aplicada na v5.0.0a1: `_new_duckdb` configura `SET home_directory='{state_dir}'`
antes de qualquer INSTALL. Se persiste:

```bash
ls -ld /dados1/state
# Deve existir e ser gravável por megalog
sudo -u megalog touch /dados1/state/.test && sudo -u megalog rm /dados1/state/.test
```

---

## Frontend mostra página em branco

### Diagnóstico

```bash
# 1. Build existe?
ls /usr/local/src/megalog-v5/frontend/dist/
# Deve ter index.html + assets/

# 2. nginx serve corretamente?
curl -s http://127.0.0.1/ | head
# Deve retornar HTML com <div id="app"></div>

# 3. Bundle JS carrega?
curl -sI http://127.0.0.1/assets/index-XXXXX.js
# Esperado: 200 OK
```

### Causas

**(a) Build não foi feito**
```bash
cd /usr/local/src/megalog-v5/frontend
sudo npm run build
```

**(b) nginx aponta para diretório errado**
```bash
grep "root " /etc/nginx/sites-enabled/megalog
# Deve apontar para /usr/local/src/megalog-v5/frontend/dist
```

**(c) Console do browser mostra erros**
- F12 → Console → veja stack trace
- F12 → Network → veja se algum chunk JS deu 404

---

## Build do frontend falha

### Causas

**(a) `npm install` falha (sem internet, npm registry inacessível)**
```bash
# Use mirror nacional
cd /usr/local/src/megalog-v5/frontend
sudo npm config set registry https://registry.npmmirror.com
sudo npm install
```

**(b) `vue-tsc` falha por erro de TypeScript**
- Veja saída do `npm run build`. Erros costumam ser tipos faltando ou imports inexistentes
- Após corrigir, rebuilder

**(c) Memória insuficiente durante build (containers/VMs pequenas)**
```bash
sudo NODE_OPTIONS=--max-old-space-size=2048 npm run build
```

---

## Não encontrou seu problema?

1. **Verifique o estado geral:**
   ```bash
   systemctl status megalog-{receiver,processor,web} nginx
   journalctl -u 'megalog-*' --since '1 hour ago' -p err --no-pager
   df -h /dados1 /dados2
   ```

2. **Aumentar verbosidade (debug):**
   ```env
   # Em /etc/megalog/megalog.env (adicionar):
   LOG_LEVEL=DEBUG
   ```
   Depois `systemctl restart megalog-...`

3. **Reportar bug:** ticket interno com `journalctl --no-pager` dos últimos 30 min e
   output dos comandos acima.
