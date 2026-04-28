# Manual do Operador — MegaLog v5

> Para o técnico que vai instalar e usar o sistema no dia-a-dia. Linguagem
> narrativa, passo-a-passo. Não exige conhecimento prévio de Python ou SQL.

## Sumário

1. [O que é o MegaLog](#1-o-que-é-o-megalog)
2. [Antes de começar](#2-antes-de-começar)
3. [Instalando](#3-instalando)
4. [Configurando o Mikrotik](#4-configurando-o-mikrotik)
5. [Primeiro acesso à interface](#5-primeiro-acesso-à-interface)
6. [Conhecendo o Dashboard](#6-conhecendo-o-dashboard)
7. [Fazendo uma busca forense](#7-fazendo-uma-busca-forense)
8. [Lendo um alerta de anomalia](#8-lendo-um-alerta-de-anomalia)
9. [Gerenciando usuários](#9-gerenciando-usuários)
10. [Rotina diária](#10-rotina-diária)
11. [Quando algo dá errado](#11-quando-algo-dá-errado)

---

## 1. O que é o MegaLog

O **MegaLog** é um sistema que recebe **logs de NAT** dos roteadores
Mikrotik da sua operadora e armazena tudo de forma compactada para que você
consiga responder uma pergunta crítica:

> *"Em 23 de abril às 15:42, alguém usando o IP público **170.245.175.121** porta
> **42115** acessou um destino que está sob investigação policial. Qual cliente
> da minha rede estava por trás desse IP?"*

Sem o MegaLog, essa resposta é praticamente impossível de obter — CGNAT mistura
milhares de assinantes em poucos IPs públicos. Com ele, você responde em
**segundos**: o IP privado responsável foi `100.80.0.119`, que é o assinante
"Fulano de Tal" (nome consultado depois no seu sistema de gestão).

Esse cumprimento é **obrigatório por lei**:
- **Marco Civil da Internet** (Lei 12.965/2014) — guardar logs por 1 ano
- **Anatel Resolução 614/2013** — disponibilizar para autoridades em até 10 dias

---

## 2. Antes de começar

### O que você precisa ter pronto

- [ ] Um servidor com Debian 12+ ou Ubuntu 22.04+, recém instalado
- [ ] Pelo menos **2 discos**:
  - SSD em `/dados1` (rápido, para dados recentes — ~30 dias)
  - HDD em `/dados2` (lento, para histórico — 365 dias)
- [ ] Acesso `root` (`sudo`) ao servidor
- [ ] IP do servidor anotado (ex: `10.100.100.2`)
- [ ] Acesso ao terminal RouterOS dos Mikrotiks que vão enviar logs
- [ ] Os Mikrotiks já com regras NAT funcionando (com `log=yes` opcional)

### Cálculo de espaço

| Cenário | Hot (30 dias) | Cold (365 dias) |
|---|---|---|
| 1 roteador, 1M logs/dia | ~270 MB | ~3.3 GB |
| 1 roteador, 10M logs/dia | ~2.7 GB | ~33 GB |
| 5 roteadores, 5M logs/dia | ~6.7 GB | ~82 GB |

Para a maioria dos casos, **/dados1 com 50 GB SSD e /dados2 com 200 GB HDD**
sobra espaço com folga.

---

## 3. Instalando

Em uma única linha:

```bash
sudo /usr/local/src/megalog-v5/deploy/install.sh
```

O instalador é interativo e vai te perguntar onde guardar os dados, em qual
porta receber syslog, etc. Para a maioria das instalações, **basta apertar Enter
em todas as perguntas** — os defaults são bons.

```
═══════════════════════════════════════════════════════════════════
  MegaLog v5 — instalação interativa
═══════════════════════════════════════════════════════════════════
Pressione Enter para aceitar o valor padrão entre [colchetes].

Diretório do código-fonte [/usr/local/src/megalog-v5]: ↵
Hot storage (SSD) [/dados1/hot]: ↵
Cold storage (HD) [/dados2/cold]: ↵
Buffer .raw [/dados1/stream]: ↵
State (registry, megalog.db) [/dados1/state]: ↵
Porta UDP do syslog [514]: ↵
Porta TCP da API [5000]: ↵
Dias em hot antes de mover p/cold [30]: ↵
Dias antes de deletar (0=nunca) [365]: ↵
```

A instalação demora **2 a 5 minutos**. No fim, o sistema mostra:

```
═══════════════════════════════════════════════════════════════════
  Instalação concluída
═══════════════════════════════════════════════════════════════════

  Web:           http://<este-host>:5000  (proxy nginx :80)
  Login default: admin / megalog123  (TROCAR no primeiro acesso)
  UDP syslog:    porta 514 (configurar Mikrotik para apontar aqui)
  Config file:   /etc/megalog/megalog.env
```

> 💡 **Anote o IP do servidor.** Você vai precisar dele duas vezes: para
> configurar o Mikrotik e para acessar a interface web.

---

## 4. Configurando o Mikrotik

Conecte no terminal do Mikrotik (Winbox → New Terminal, ou SSH) e cole os
comandos abaixo, **substituindo `10.100.100.2` pelo IP do seu servidor MegaLog**:

```routeros
# 1. Criar a "ação" de log remoto
/system logging action
add name=megalog \
    target=remote \
    remote=10.100.100.2 \
    remote-port=514 \
    bsd-syslog=yes \
    syslog-facility=daemon \
    syslog-severity=info

# 2. Direcionar o tópico "firewall" para essa ação
/system logging
add action=megalog topics=firewall
```

Se você ainda não tem regras NAT logando, adicione `log=yes` nelas:

```routeros
/ip firewall nat
print
# Para cada regra de masquerade/srcnat que você quer logar:
set [find action=masquerade] log=yes log-prefix="CGNAT:"
```

### Confirmando que chegou

No servidor, abra outro terminal SSH e rode:

```bash
sudo tcpdump -i any udp port 514 -n -c 10
```

Você deve ver pacotes chegando do IP do Mikrotik. Se vir `0 packets captured`
após 30 segundos, alguma coisa no caminho está bloqueando — veja
[TROUBLESHOOTING — Receiver não recebe](TROUBLESHOOTING.md#receiver-não-recebe-pacotes-do-mikrotik).

Depois, no servidor:

```bash
ls -lh /dados1/stream/
```

Você deve ver um arquivo da hora atual crescendo (ex: `2026-04-27-17.raw`).

---

## 5. Primeiro acesso à interface

No seu PC, abra o navegador e digite:

```
http://10.100.100.2/
```

(substituindo pelo IP do servidor).

Você verá a tela de login:

```
┌──────────────────────────────┐
│           ╔═════╗            │
│           ║ ML  ║            │
│           ╚═════╝            │
│         MegaLog              │
│  Sistema de auditoria CGNAT  │
│                              │
│  USUÁRIO                     │
│  [admin                  ]   │
│  SENHA                       │
│  [megalog123             ]   │
│                              │
│  [        Entrar →       ]   │
└──────────────────────────────┘
```

Use:
- **Usuário:** `admin`
- **Senha:** `megalog123`

> ⚠️ **A primeira coisa a fazer é trocar essa senha.** Vai em "Trocar senha"
> na sidebar. Use uma senha forte (mínimo 8 caracteres, recomendado >12).

---

## 6. Conhecendo o Dashboard

Após login, você cai no Dashboard. Vamos passar por cada seção:

### HARDWARE (4 cards no topo)

```
┌─CPU─────┐ ┌─RAM─────┐ ┌─DISCO HOT─┐ ┌─DISCO COLD┐
│  6.1%   │ │  31.7%  │ │   0.1%    │ │   0.2%    │
│ █▌▎▌▎▌  │ │ ████░░  │ │ ░░░░░░░   │ │ ░░░░░░░   │
│ load... │ │ 4.9 GB..│ │ livre 104 │ │ livre 54  │
└─────────┘ └─────────┘ └───────────┘ └───────────┘
```

- **CPU:** uso médio + barras por core. Verde até 50%, amarelo 50-80%, vermelho >80%
- **RAM:** % usada + barra colorida + bytes
- **Disco HOT (SSD):** onde ficam os DBs do mês atual
- **Disco COLD (HDD):** onde ficam os Parquets do histórico

### INGESTÃO (4 cards meio)

```
┌─BUFFER .RAW────┐ ┌─LOGS HOJE──┐ ┌─PROCESSADOR─┐ ┌─RECEIVER UDP┐
│   9.4 MB       │ │   125.650  │ │  ● ACTIVE   │ │  ● ACTIVE   │
│ último há 4s   │ │ DB 6.8 MB  │ │ tail batch  │ │ porta 514   │
└────────────────┘ └────────────┘ └─────────────┘ └─────────────┘
```

- **Buffer .raw:** quanto tem no arquivo de logs brutos (cresce em segundos, depois cai quando o processor consome)
- **Logs hoje:** quantos logs já entraram no DuckDB do dia
- **Processador / Receiver:** ambos devem estar **● ACTIVE** verdes pulsando

> 🚨 Se algum desses 2 últimos estiver vermelho ou amarelo, vá para
> [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

### ACESSO RÁPIDO — ÚLTIMOS 14 DIAS

Tirinha de "cards de dia". O dia atual aparece em **destacado em ciano** à esquerda:

```
┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐
│SEG│ │QUI│ │QUA│ │TER│ │SEG│  ← ciano = hoje
│ 27│ │ 23│ │ 22│ │ 21│ │ 20│
│63k│ │2.7M│ │2.7M│ │2.3M│ │2.7M│  ← logs do dia
│ ● │ │ ▣ │ │ ▣ │ │ ▣ │ │ ▣ │  ← ● HOT  ▣ COLD  ⚠ anomalia
└───┘ └───┘ └───┘ └───┘ └───┘
```

Clique em qualquer dia para ir direto para a busca filtrada por essa data.

### SERVIÇOS SYSTEMD (tabela embaixo)

Mostra cada serviço com estado e descrição. Tudo deve estar `● ACTIVE`.

---

## 7. Fazendo uma busca forense

**Cenário típico:** Você recebeu um ofício pedindo identificação de
quem usou o IP público `170.245.175.121` na porta `42115` no dia 23/04/2026 às
16h10 (mais ou menos).

### Passo 1: Vá em "Busca forense" na sidebar

### Passo 2: Preencha os filtros

- **Data:** 2026-04-23
- **IP NAT (público):** 170.245.175.121
- **Porta NAT:** 42115
- **Hora inicial:** 16:00:00
- **Hora final:** 16:30:00

Os outros campos deixe vazios.

### Passo 3: Clique em "Buscar"

Em ~50 ms, aparece a tabela de resultados:

```
HORA      ORIGEM (PRIVADO)        DESTINO              NAT (PÚBLICO)        PROTO
16:10:35  100.80.0.61:42115       45.166.4.234:443     170.245.175.121:42115 UDP
16:10:35  100.80.0.61:42115       45.181.185.106:443   170.245.175.121:42115 UDP
16:10:35  100.80.0.61:42115       186.219.166.203:80   170.245.175.121:42115 UDP
```

### Passo 4: Identifique o IP privado

A coluna **"Origem (privado)"** mostra `100.80.0.61` — esse é o IP da rede
interna que estava com a porta 42115 do IP público naquele momento.

### Passo 5: Cruze com seu sistema de gestão

`100.80.0.61` provavelmente é um IP CGNAT do bloco `100.64.0.0/10` que sua
rede atribui via DHCP/PPP. No seu sistema RADIUS / OLT / DHCP, consulte
quem tinha esse IP em **23/04/2026 16:10:35**. Esse é o assinante.

### Passo 6 (opcional): Exporte para anexar no ofício

Clique em "↓ Exportar CSV" no canto superior direito da página. Você baixa
um arquivo `megalog-2026-04-23.csv` com **todos** os resultados (até 100k
linhas). Anexe isso ao seu PDF de resposta ao ofício.

> 💡 **Toda busca e export ficam registrados** no audit log com seu usuário
> e IP de origem. É registro permanente — útil para auditoria interna.

---

## 8. Lendo um alerta de anomalia

Todos os dias às 02:30 da manhã, o sistema analisa o volume do dia e compara
com a média dos 7 dias anteriores. Se o volume for **3× maior** que o normal,
gera um alerta.

### Onde aparecem

- Badge laranja na sidebar (ao lado de "Alertas" no menu Admin)
- Card laranja no Dashboard (se tiver pendentes)
- Lista completa em **Admin → Alertas**

### Como ler um alerta

Clique num alerta para abrir o detalhe. Você verá:

#### Resumo (4 cards no topo)

```
┌─VOLUME──────┐ ┌─RAZÃO───────┐ ┌─CONFIANÇA──┐ ┌─DB DIA──────┐
│ 2.651.065   │ │   3.3×      │ │    65%     │ │  22.8 MB    │
│ esperado    │ │ acima do    │ │ Flood/Loop │ │ criado      │
│ 800.000     │ │ normal      │ │            │ │ ...         │
└─────────────┘ └─────────────┘ └────────────┘ └─────────────┘
```

#### Por que essa classificação

```
▸ Fluxo único repetido 85.540 vezes — tráfego automatizado suspeito
▸ Cliente principal 192.168.20.105 com 3.708 conexões repetidas
```

O classificador analisa **9 padrões** de comportamento e escolhe o mais
provável. As "razões" explicam quais sinais levaram à classificação.

#### Possíveis causas

```
◦ Equipamento em loop: TV Box, câmera IP ou roteador com firmware defeituoso
◦ Daemon (ntpd, chronyd, openvpn) com retry sem backoff em falha de rede
◦ Keepalive ultra-agressivo de aplicativo de monitoramento ou VoIP
```

Lista de causas comuns para essa classificação. Use como ponto de partida
da investigação.

#### Distribuição de portas

Gráfico de barras mostrando se o tráfego é em portas bem-conhecidas
(0-1023), registradas (1024-49151) ou efêmeras (49152+). Padrões anômalos:
- Muito tráfego em porta 123 (NTP) → equipamento em loop NTP
- Muito tráfego em portas efêmeras + muitos destinos → P2P/Torrent
- Tráfego concentrado em poucas portas + 1 origem → ataque DoS

#### Top 3 tabelas

- **Top IPs origem** — quem mais gerou conexões (cliente suspeito)
- **Top portas destino** — qual serviço foi mais acessado (DNS, HTTPS, NTP, etc)
- **Top IPs destino** — para onde foram as conexões

#### Análise narrativa completa

Texto fixo gerado pelo classificador, útil para colar em ticket interno
ou relatório.

### Reconhecendo o alerta

Quando você terminar de investigar (ou se for falso positivo), clique em
**"Reconhecer"** no canto superior direito. O alerta vai para a lista de
"reconhecidos" (ainda visível, mas não conta no badge).

---

## 9. Gerenciando usuários

**Admin → Usuários** mostra a lista. Há 2 papéis:

| Papel | Pode |
|---|---|
| `user` | Ver dashboard, fazer busca, ver alertas |
| `admin` | Tudo + criar/desativar usuários, ver auditoria, reconhecer alertas |

### Criar um novo usuário

Clique em "+ Novo usuário". Aparece um modal:

```
┌─ Novo usuário ────────────────┐
│ USUÁRIO                       │
│ [técnico1                  ]  │
│ SENHA (mín. 8 chars)          │
│ [...........               ]  │
│ PAPEL                         │
│ [user — acesso a busca... ▾]  │
│                               │
│        [Cancelar] [Criar]     │
└───────────────────────────────┘
```

A senha precisa ter no mínimo 8 caracteres. Anote-a e passe para o usuário
em canal seguro (não por email simples). O usuário **deve trocar no primeiro
acesso** via "Trocar senha" na sidebar.

### Resetar senha de um usuário

Passa o mouse sobre a linha do usuário e aparece "resetar senha". Você
digita a nova senha (provisória), e ele troca no próximo login.

### Desativar usuário

Mesma área de hover, opção "desativar" (vermelha). Não apaga — só impede
login. O histórico de auditoria do usuário é preservado.

> ⚠️ Você não pode desativar a si mesmo (proteção contra lock-out).

---

## 10. Rotina diária

### O que o sistema faz sozinho

**Sempre:**
- Receber syslog (24/7)
- Processar e indexar
- Auto-restart em caso de crash (systemd)

**Toda noite às 02:00:**
- Move DBs antigos (>30 dias) de hot para cold (Parquet zstd)
- Atualiza estatísticas diárias

**Toda noite às 02:30:**
- Detecta anomalias (volume 3× acima do baseline de 7 dias)
- Classifica em uma das 9 categorias
- Cria alerta automático

**Toda segunda às 03:00:**
- Apaga Parquets antigos (>365 dias) — cumpre Marco Civil

### O que você precisa fazer

**Diário (5 minutos):**
- Abrir o dashboard
- Verificar que os 4 cards de Hardware estão verdes/amarelos (não vermelhos)
- Verificar que Receiver e Processor estão ACTIVE
- Verificar se há alertas novos no badge da sidebar (tem? abrir e investigar)

**Semanal (~10 min):**
- Conferir Logs diários: o volume do dia anterior está dentro do esperado?
  (variações de 2× são normais; >5× ou queda brusca = investigar)
- Espaço em disco hot/cold ainda confortável?
- Auditoria: olhar últimos logins, alguém estranho?

**Mensal:**
- Conferir tamanho do `journalctl --disk-usage` — se >1GB, vacuumar
- Backup dos arquivos críticos (ver [OPERATIONS.md §4](OPERATIONS.md#4-backup))

**Anual:**
- Trocar SECRET_KEY (ver [SECURITY.md §4](SECURITY.md#4-rotação-de-chaves-e-credenciais))
- Revisar usuários (algum técnico saiu? desativar)
- Rodar auditoria automatizada (ver [SECURITY.md §6](SECURITY.md#auditoria-periódica))

---

## 11. Quando algo dá errado

### Dashboard "vermelho" — algum card preocupante

| Sintoma | Provável causa | Solução |
|---|---|---|
| CPU >80% constante | Muito tráfego na ingestão | Normal em pico; se persiste, considere mais cores |
| RAM >90% | Cache do DuckDB ou IP cache muito grande | Considere baixar `MEGALOG_IP_CACHE_MAX` ou adicionar RAM |
| Disco HOT >85% | DBs do mês atual ocupando muito | Diminua `MEGALOG_HOT_RETENTION_DAYS` para 14 ou 7 |
| Disco COLD >85% | Histórico velho acumulado | Confirme que retention timer está rodando; ajuste `DELETE_AFTER_DAYS` |
| Receiver INACTIVE | Crash do receiver | `journalctl -u megalog-receiver -n 50` |
| Processor INACTIVE | Crash do processor | `journalctl -u megalog-processor -n 50` |

### Buffer .raw subindo, Logs hoje parado

Sinal claro de que o **processor parou**. O receiver está OK (ainda escreve
no .raw), mas ninguém consome.

```bash
sudo systemctl restart megalog-processor
sudo journalctl -u megalog-processor -f
```

### "Não consigo logar"

- Confirmar que `https` não está habilitado (`http://`)
- Limpar cookies do site no navegador (Ctrl+Shift+Del)
- Verificar Caps Lock
- Se esqueceu a senha do admin único, ver [TROUBLESHOOTING — Login falha](TROUBLESHOOTING.md#login-falha-mesmo-com-senha-correta)

### Busca não retorna nada

- A data está certa? (calendário do dashboard mostra os dias com dados)
- Os IPs estão completos? (`100.80.0.119` não `100.80.0` ou `100.80.119`)
- Tente sem alguns filtros para ver se há dados na data
- Se o dia atual ainda não tem dados (acabou de instalar), espere alguns minutos

### Mais soluções

[docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md) tem receitas detalhadas para
cada problema conhecido.

---

## Glossário

| Termo | Significado |
|---|---|
| **CGNAT** | Carrier-Grade NAT — várias dezenas de assinantes compartilham um IP público |
| **NAT** | Network Address Translation — tradução de IP privado para público |
| **Syslog** | Protocolo padrão (UDP 514) para envio de logs de equipamentos de rede |
| **Hot storage** | Disco rápido (SSD) onde ficam dados recentes (últimos 30 dias) |
| **Cold storage** | Disco lento (HDD) onde fica o histórico comprimido (até 365 dias) |
| **DuckDB** | Banco de dados colunar usado no hot — rápido para escrita |
| **Parquet** | Formato de arquivo colunar comprimido usado no cold |
| **systemd** | Sistema de inicialização do Linux que roda os serviços |
| **Mikrotik** | Marca de roteadores muito usada em provedores de Internet |
| **RouterOS** | Sistema operacional dos roteadores Mikrotik |
| **Marco Civil** | Lei 12.965/2014 — regula a Internet no Brasil, exige guardar logs |
| **Anatel** | Agência reguladora de telecomunicações no Brasil |

---

## Próximos passos

- Quer entender por dentro? [docs/ARCHITECTURE.md](ARCHITECTURE.md)
- Vai administrar via terminal? [docs/OPERATIONS.md](OPERATIONS.md)
- Vai integrar com outro sistema? [docs/API.md](API.md)
- Quer endurecer a segurança? [docs/SECURITY.md](SECURITY.md)
