<template>
  <PageHeader title="Dashboard" subtitle="Status do servidor e ingestão em tempo real">
    <template #actions>
      <span class="text-text-secondary text-[12px] font-mono num">{{ clock }}</span>
    </template>
  </PageHeader>

  <div class="p-6 space-y-6 animate-fade-in">

    <!-- ── Hardware ─────────────────────────────────────────────────────── -->
    <section>
      <h2 class="label-micro mb-3">Hardware</h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">

        <!-- CPU -->
        <div class="card card-hover p-4">
          <div class="flex items-start justify-between mb-3">
            <span class="label-micro">CPU</span>
            <span class="num text-stat font-bold text-text-primary">
              <Skeleton v-if="loading" width="48px" height="20px" />
              <template v-else>{{ status?.cpu.avg_pct.toFixed(1) }}<span class="text-[14px] text-text-muted ml-0.5">%</span></template>
            </span>
          </div>
          <CpuBars v-if="status" :cores="status.cpu.per_core_pct" />
          <Skeleton v-else height="40px" />
          <div class="mt-2 text-[11px] text-text-muted num">
            load {{ status?.cpu.load_avg.map(n => n.toFixed(2)).join(' / ') ?? '—' }}
          </div>
        </div>

        <!-- RAM -->
        <div class="card card-hover p-4">
          <div class="flex items-start justify-between mb-3">
            <span class="label-micro">Memória RAM</span>
            <span class="num text-stat font-bold text-text-primary">
              <Skeleton v-if="loading" width="48px" height="20px" />
              <template v-else>{{ status?.ram.used_pct.toFixed(1) }}<span class="text-[14px] text-text-muted ml-0.5">%</span></template>
            </span>
          </div>
          <ProgressBar v-if="status" :value="status.ram.used_pct" />
          <Skeleton v-else height="6px" />
          <div class="mt-2 text-[11px] text-text-muted num">
            {{ fmtBytes(status?.ram.used_bytes) }} <span class="text-text-dim">de</span> {{ fmtBytes(status?.ram.total_bytes) }}
          </div>
        </div>

        <!-- Disco HOT -->
        <div class="card card-hover p-4">
          <div class="flex items-start justify-between mb-3">
            <span class="label-micro">Disco HOT (SSD)</span>
            <span class="num text-stat font-bold text-text-primary">
              <Skeleton v-if="loading" width="48px" height="20px" />
              <template v-else>{{ status?.disks.hot?.used_pct.toFixed(1) ?? '—' }}<span class="text-[14px] text-text-muted ml-0.5">%</span></template>
            </span>
          </div>
          <ProgressBar v-if="status?.disks.hot" :value="status.disks.hot.used_pct" />
          <Skeleton v-else height="6px" />
          <div class="mt-2 text-[11px] text-text-muted num">
            <template v-if="status?.disks.hot">livre {{ fmtBytes(status.disks.hot.free_bytes) }} <span class="text-text-dim">de</span> {{ fmtBytes(status.disks.hot.total_bytes) }}</template>
          </div>
        </div>

        <!-- Disco COLD -->
        <div class="card card-hover p-4">
          <div class="flex items-start justify-between mb-3">
            <span class="label-micro">Disco COLD (HDD)</span>
            <span class="num text-stat font-bold text-text-primary">
              <Skeleton v-if="loading" width="48px" height="20px" />
              <template v-else>{{ status?.disks.cold?.used_pct.toFixed(1) ?? '—' }}<span class="text-[14px] text-text-muted ml-0.5">%</span></template>
            </span>
          </div>
          <ProgressBar v-if="status?.disks.cold" :value="status.disks.cold.used_pct" />
          <Skeleton v-else height="6px" />
          <div class="mt-2 text-[11px] text-text-muted num">
            <template v-if="status?.disks.cold">livre {{ fmtBytes(status.disks.cold.free_bytes) }} <span class="text-text-dim">de</span> {{ fmtBytes(status.disks.cold.total_bytes) }}</template>
          </div>
        </div>
      </div>
    </section>

    <!-- ── Ingestão (estado atual do pipeline) ──────────────────────────── -->
    <section>
      <h2 class="label-micro mb-3">Ingestão</h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">

        <div class="card p-4">
          <div class="flex items-start justify-between mb-2">
            <span class="label-micro">Buffer .raw</span>
            <span class="num text-stat font-bold text-text-primary">
              <Skeleton v-if="loading" width="60px" height="22px" />
              <template v-else>{{ fmtBytes(status?.ingest.raw_buffer_bytes ?? 0) }}</template>
            </span>
          </div>
          <div class="text-[11px] text-text-muted">
            <template v-if="status?.ingest.raw_last_seen">
              último pacote {{ relativeTime(status.ingest.raw_last_seen) }}
            </template>
            <template v-else>aguardando dados…</template>
          </div>
        </div>

        <div class="card p-4">
          <div class="flex items-start justify-between mb-2">
            <span class="label-micro">Logs hoje</span>
            <span class="num text-stat font-bold text-text-primary">
              <Skeleton v-if="loading && lastLogCount === null" width="80px" height="22px" />
              <template v-else-if="lastLogCount !== null">{{ fmtNumber(lastLogCount) }}</template>
              <template v-else>—</template>
            </span>
          </div>
          <div class="text-[11px] text-text-muted num">
            <template v-if="status?.ingest.today_db_size">
              DB {{ fmtBytes(status.ingest.today_db_size) }}
              <span v-if="status?.ingest.today_count_age_seconds && status.ingest.today_count_age_seconds > 5"
                    class="text-text-dim ml-1">
                · há {{ status.ingest.today_count_age_seconds.toFixed(0) }}s
              </span>
            </template>
            <template v-else>sem partição</template>
          </div>
        </div>

        <div class="card p-4">
          <div class="flex items-start justify-between mb-2">
            <span class="label-micro">Processador</span>
            <StatusDot :state="serviceState(status?.services.processor)" :pulse="status?.services.processor === 'active'">
              {{ status?.services.processor ?? 'desconhecido' }}
            </StatusDot>
          </div>
          <div class="text-[11px] text-text-muted">
            tail batch · flush {{ '~3s' }}
          </div>
        </div>

        <div class="card p-4">
          <div class="flex items-start justify-between mb-2">
            <span class="label-micro">Receiver UDP</span>
            <StatusDot :state="serviceState(status?.services.receiver)" :pulse="status?.services.receiver === 'active'">
              {{ status?.services.receiver ?? 'desconhecido' }}
            </StatusDot>
          </div>
          <div class="text-[11px] text-text-muted">porta 514 · syslog Mikrotik</div>
        </div>
      </div>
    </section>

    <!-- ── Alertas pendentes ────────────────────────────────────────────── -->
    <section v-if="alertsStore.unackCount > 0">
      <h2 class="label-micro mb-3 text-accent-amber">⚠ Anomalias não reconhecidas</h2>
      <div class="space-y-2">
        <RouterLink
          v-for="a in alertsStore.items.slice(0, 3)"
          :key="a.id"
          :to="`/anomaly/${a.date}`"
          class="card flex items-center gap-4 px-4 py-3 hover:bg-bg-elevated border-l-2 !border-l-accent-amber transition-colors"
        >
          <div class="flex-1">
            <div class="flex items-baseline gap-3 mb-1">
              <span class="text-[14px] font-semibold text-text-primary num">{{ a.date }}</span>
              <span class="badge-warn">{{ a.ratio.toFixed(1) }}× acima do normal</span>
              <span class="text-[12px] text-text-secondary">{{ a.classification }}</span>
            </div>
            <div class="text-[12px] text-text-muted">
              {{ fmtNumber(a.log_count) }} logs · esperado ≈ {{ fmtNumber(a.expected_count) }}
            </div>
          </div>
          <span class="text-text-dim text-lg">›</span>
        </RouterLink>
        <RouterLink to="/admin/alerts" class="block text-center text-[12px] text-text-secondary hover:text-text-primary py-1">
          Ver todos ({{ alertsStore.unackCount }}) →
        </RouterLink>
      </div>
    </section>

    <!-- ── Acesso rápido — últimos 14 dias ──────────────────────────────── -->
    <section>
      <h2 class="label-micro mb-3">Acesso rápido — últimos 14 dias</h2>
      <div v-if="dailyLoading" class="grid grid-cols-7 lg:grid-cols-14 gap-2">
        <Skeleton v-for="i in 14" :key="i" height="64px" />
      </div>
      <div v-else-if="last14.length === 0" class="card">
        <EmptyState
          title="Nenhum dia indexado ainda"
          subtitle="Os dias aparecem aqui quando o processor confirmar o primeiro batch e o job archive popular daily_stats. Aguarde alguns minutos após a primeira ingestão."
          icon="◷"
        />
      </div>
      <div v-else class="grid grid-cols-7 lg:grid-cols-14 gap-2">
        <RouterLink
          v-for="day in last14"
          :key="day.date"
          :to="day.alert ? `/anomaly/${day.date}` : `/search?date=${day.date}`"
          class="card card-hover px-2 py-3 flex flex-col items-center gap-1 group"
          :class="day.date === todayStr && 'border-primary bg-primary-bg'"
        >
          <span class="text-[9px] font-bold uppercase tracking-wider text-text-muted">
            {{ weekday(day.date) }}
          </span>
          <span class="num text-[20px] font-bold leading-none text-text-primary group-hover:text-primary transition-colors">
            {{ day.date.slice(8) }}
          </span>
          <span class="text-[10px] text-text-muted num">
            {{ shortNumber(day.log_count) }}
          </span>
          <span v-if="day.alert" class="text-accent-amber text-[10px]">⚠</span>
          <span v-else-if="day.kind === 'cold'" class="text-text-dim text-[10px]" title="Cold storage">▣</span>
          <span v-else-if="day.kind === 'hot'" class="text-accent-green text-[10px]" title="Hot storage">●</span>
        </RouterLink>
      </div>
    </section>

    <!-- ── Serviços (detalhe) ──────────────────────────────────────────── -->
    <section>
      <h2 class="label-micro mb-3">Serviços systemd</h2>
      <div class="card overflow-hidden">
        <table class="tbl">
          <thead><tr><th>Unit</th><th>Estado</th><th>Descrição</th></tr></thead>
          <tbody>
            <tr v-for="(state, name) in (status?.services ?? {})" :key="name">
              <td class="mono text-text-primary">megalog-{{ name }}.service</td>
              <td>
                <StatusDot :state="serviceState(state)">{{ state }}</StatusDot>
              </td>
              <td class="text-[12px] text-text-secondary">{{ serviceDescription(name) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RouterLink } from "vue-router";
import PageHeader from "@/components/PageHeader.vue";
import StatusDot from "@/components/StatusDot.vue";
import ProgressBar from "@/components/ProgressBar.vue";
import CpuBars from "@/components/CpuBars.vue";
import EmptyState from "@/components/EmptyState.vue";
import Skeleton from "@/components/Skeleton.vue";
import { api, type SystemStatus, type DailyRow } from "@/lib/api";
import { fmtBytes, fmtNumber } from "@/lib/format";
import { useAlertsStore } from "@/stores/alerts";

const status = ref<SystemStatus | null>(null);
const calendar = ref<DailyRow[]>([]);
const loading = ref(true);
const dailyLoading = ref(true);
const clock = ref("");
const alertsStore = useAlertsStore();

// Cache do último valor conhecido — defesa contra qualquer "flicker" residual
// caso a API devolva null por contenção de lock no DuckDB hot do dia.
const lastLogCount = ref<number | null>(null);

const todayStr = new Date().toISOString().slice(0, 10);

async function refreshStatus() {
  try {
    const r = await api.get<SystemStatus>("/api/system-status");
    status.value = r.data;
    // só atualiza o cache se a API devolveu valor válido
    if (r.data.ingest.today_log_count !== null) {
      lastLogCount.value = r.data.ingest.today_log_count;
    }
  } catch { /* silencioso */ }
  finally { loading.value = false; }
}

async function refreshCalendar() {
  try {
    calendar.value = (await api.get<DailyRow[]>("/api/analytics/daily")).data;
  } finally { dailyLoading.value = false; }
}

const last14 = computed(() => {
  // Últimos 14 dias, do mais recente (esquerda) para o mais antigo (direita).
  return [...calendar.value].slice(-14).reverse();
});

function tickClock() {
  clock.value = new Date().toLocaleString("pt-BR");
}

let tStatus: number | undefined;
let tClock: number | undefined;

onMounted(() => {
  refreshStatus();
  refreshCalendar();
  alertsStore.refresh();
  tickClock();
  tStatus = window.setInterval(refreshStatus, 3000);
  tClock  = window.setInterval(tickClock, 1000);
});
onUnmounted(() => {
  if (tStatus) clearInterval(tStatus);
  if (tClock) clearInterval(tClock);
});

function serviceState(s: string | undefined): "ok" | "warn" | "error" | "muted" {
  if (s === "active") return "ok";
  if (s === "failed") return "error";
  if (s === "unknown" || s === undefined) return "muted";
  return "warn";
}

function serviceDescription(name: string | number) {
  const n = String(name);
  return {
    receiver:  "Recebe syslog UDP do Mikrotik na porta 514 e grava em .raw rotacionado por hora",
    processor: "Lê o .raw, parseia formatos Mikrotik (4 timestamps + line continuation), insere em batch no DuckDB diário",
    web:       "FastAPI + Uvicorn — esta interface, busca forense, analytics, admin",
  }[n] ?? "";
}

function weekday(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "").toUpperCase();
}

function shortNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || n === 0) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000)     return (n / 1_000).toFixed(0) + "k";
  return String(n);
}

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - t) / 1000;
  if (diff < 60)    return `há ${diff.toFixed(0)}s`;
  if (diff < 3600)  return `há ${(diff / 60).toFixed(0)}m`;
  if (diff < 86400) return `há ${(diff / 3600).toFixed(1)}h`;
  return `há ${(diff / 86400).toFixed(0)}d`;
}
</script>
