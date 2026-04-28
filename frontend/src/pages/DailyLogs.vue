<template>
  <PageHeader title="Logs diários" subtitle="Volume por dia, status hot/cold e indicador de anomalia" />

  <div class="p-6">
    <div v-if="loading" class="card p-4 space-y-2">
      <Skeleton v-for="i in 5" :key="i" height="36px" />
    </div>

    <div v-else-if="rows.length === 0" class="card">
      <EmptyState
        title="Nenhum dia registrado ainda"
        subtitle="Quando o job archive (02:00) rodar pela primeira vez, os dias aparecerão aqui."
        icon="◷"
      />
    </div>

    <div v-else class="space-y-5">

      <!-- Sumário (largura completa) -->
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div class="card p-3">
          <div class="label-micro">Dias indexados</div>
          <div class="num text-stat font-bold text-text-primary mt-1">{{ rows.length }}</div>
        </div>
        <div class="card p-3">
          <div class="label-micro">Total de logs</div>
          <div class="num text-stat font-bold text-text-primary mt-1">{{ fmtNumber(totalCount) }}</div>
        </div>
        <div class="card p-3">
          <div class="label-micro">Volume total</div>
          <div class="num text-stat font-bold text-text-primary mt-1">{{ fmtBytes(totalSize) }}</div>
        </div>
        <div class="card p-3">
          <div class="label-micro">Média por dia</div>
          <div class="num text-stat font-bold text-text-primary mt-1">{{ fmtNumber(avgPerDay) }}</div>
        </div>
      </div>

      <!-- Conteúdo principal: lista + painel lateral -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">

        <!-- Coluna esquerda: lista de dias -->
        <div class="lg:col-span-2 space-y-1.5">
          <div class="label-micro mb-2 px-1">Histórico de partições</div>
          <ul class="space-y-1.5">
            <li v-for="r in [...rows].reverse()" :key="r.date">
              <RouterLink
                :to="r.alert ? `/anomaly/${r.date}` : `/search?date=${r.date}`"
                class="card card-hover flex items-center gap-4 px-4 py-3 group"
                :class="r.alert && '!border-l-2 !border-l-accent-amber'"
              >
                <div class="flex flex-col min-w-[110px]">
                  <span class="font-mono text-[14px] font-semibold text-text-primary">{{ r.date }}</span>
                  <span class="text-[10.5px] text-text-muted uppercase tracking-wider mt-0.5">{{ weekdayShort(r.date) }}</span>
                </div>
                <div class="flex-1 flex items-baseline gap-5 flex-wrap">
                  <span class="num text-[15px] font-semibold text-text-primary">
                    {{ fmtNumber(r.log_count) }}<span class="text-[11px] text-text-muted ml-1">logs</span>
                  </span>
                  <span class="num text-[13px] text-text-secondary">{{ fmtBytes(r.db_size_bytes) }}</span>
                  <span class="badge-muted">{{ r.format ?? '—' }}</span>
                </div>
                <div class="flex items-center gap-3">
                  <span v-if="r.alert" class="badge-warn">⚠ investigar</span>
                  <StatusDot :state="storageState(r.kind)">{{ r.kind }}</StatusDot>
                  <span class="text-text-dim text-lg group-hover:text-primary transition-colors">›</span>
                </div>
              </RouterLink>
            </li>
          </ul>
        </div>

        <!-- Coluna direita: insights -->
        <aside class="space-y-4">

          <!-- Distribuição de storage -->
          <div class="card">
            <div class="card-header"><span class="card-title">Distribuição</span></div>
            <div class="card-body space-y-3">
              <div v-for="row in storageBreakdown" :key="row.label">
                <div class="flex items-center justify-between mb-1">
                  <span class="flex items-center gap-2 text-[12.5px] text-text-secondary">
                    <span class="dot" :class="row.dotClass" /> {{ row.label }}
                  </span>
                  <span class="num text-[12.5px] text-text-primary">{{ row.count }} <span class="text-text-muted">({{ row.pct }}%)</span></span>
                </div>
                <div class="h-1 rounded-full bg-white/[.04] overflow-hidden">
                  <div class="h-full rounded-full" :class="row.barClass" :style="{ width: row.pct + '%' }" />
                </div>
              </div>
            </div>
          </div>

          <!-- Próximas tarefas -->
          <div class="card">
            <div class="card-header"><span class="card-title">Próximas tarefas</span></div>
            <div class="card-body space-y-3">
              <div v-for="t in upcomingJobs" :key="t.name" class="flex items-start gap-3">
                <span class="dot dot-muted mt-1.5" />
                <div class="flex-1 min-w-0">
                  <div class="flex items-baseline justify-between gap-2">
                    <span class="text-[13px] font-semibold text-text-primary">{{ t.label }}</span>
                    <span class="num text-[11px] text-text-muted whitespace-nowrap">em {{ t.untilHuman }}</span>
                  </div>
                  <div class="text-[11.5px] text-text-secondary mt-0.5">{{ t.desc }}</div>
                  <div class="text-[10.5px] text-text-muted font-mono mt-0.5">{{ t.nextHuman }}</div>
                </div>
              </div>
            </div>
          </div>

          <!-- Volume nos últimos 14 dias -->
          <div v-if="rows.length > 1" class="card">
            <div class="card-header"><span class="card-title">Volume — últimos 14 dias</span></div>
            <div class="card-body">
              <div class="flex items-end gap-1 h-24">
                <div
                  v-for="d in last14"
                  :key="d.date"
                  class="flex-1 rounded-t-sm transition-colors"
                  :class="d.alert ? 'bg-accent-amber' : 'bg-primary/40 hover:bg-primary'"
                  :style="{ height: Math.max(4, (d.log_count / maxLogCount) * 100) + '%' }"
                  :title="`${d.date}: ${fmtNumber(d.log_count)}`"
                />
              </div>
              <div class="flex justify-between mt-2 text-[10px] text-text-muted font-mono">
                <span>{{ last14[0]?.date.slice(5) }}</span>
                <span>{{ last14[last14.length - 1]?.date.slice(5) }}</span>
              </div>
            </div>
          </div>

        </aside>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";
import Skeleton from "@/components/Skeleton.vue";
import StatusDot from "@/components/StatusDot.vue";
import { api, type DailyRow } from "@/lib/api";
import { fmtBytes, fmtNumber } from "@/lib/format";

const rows = ref<DailyRow[]>([]);
const loading = ref(true);

onMounted(async () => {
  try { rows.value = (await api.get<DailyRow[]>("/api/analytics/daily")).data; }
  finally { loading.value = false; }
});

const totalCount = computed(() => rows.value.reduce((s, r) => s + r.log_count, 0));
const totalSize  = computed(() => rows.value.reduce((s, r) => s + r.db_size_bytes, 0));
const avgPerDay  = computed(() => rows.value.length ? Math.round(totalCount.value / rows.value.length) : 0);

const last14 = computed(() => rows.value.slice(-14));
const maxLogCount = computed(() => Math.max(1, ...last14.value.map(d => d.log_count)));

const storageBreakdown = computed(() => {
  const total = Math.max(1, rows.value.length);
  const hot  = rows.value.filter(r => r.kind === "hot").length;
  const cold = rows.value.filter(r => r.kind === "cold").length;
  return [
    { label: "Hot (DuckDB)",   count: hot,  pct: Math.round(hot  / total * 100), dotClass: "dot-ok",    barClass: "bg-accent-green" },
    { label: "Cold (Parquet)", count: cold, pct: Math.round(cold / total * 100), dotClass: "dot-muted", barClass: "bg-text-muted" },
  ];
});

const upcomingJobs = computed(() => [
  buildJob("archive",   "Arquivamento",  "Move DuckDB hot → Parquet zstd em cold",        nextDailyAt(2, 0)),
  buildJob("analyze",   "Análise",       "Detecta anomalias e gera alertas classificados", nextDailyAt(2, 30)),
  buildJob("retention", "Retenção",      "Apaga Parquets mais antigos que delete_after",   nextWeeklyAt(1, 3, 0)),
]);

function buildJob(name: string, label: string, desc: string, next: Date) {
  return { name, label, desc, untilHuman: humanizeDelta(next), nextHuman: next.toLocaleString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) };
}

function nextDailyAt(h: number, m: number): Date {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  if (d.getTime() <= Date.now()) d.setDate(d.getDate() + 1);
  return d;
}

function nextWeeklyAt(weekday: number, h: number, m: number): Date {
  // weekday: 0=Sun, 1=Mon, ...
  const d = new Date();
  d.setHours(h, m, 0, 0);
  const diff = (weekday + 7 - d.getDay()) % 7;
  if (diff === 0 && d.getTime() <= Date.now()) d.setDate(d.getDate() + 7);
  else d.setDate(d.getDate() + diff);
  return d;
}

function humanizeDelta(target: Date): string {
  const diffMs = target.getTime() - Date.now();
  const m = Math.max(0, Math.floor(diffMs / 60000));
  if (m < 60) return `${m}min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h${m % 60 ? `${m % 60}min` : ""}`;
  return `${Math.floor(h / 24)}d${(h % 24) ? `${h % 24}h` : ""}`;
}

function storageState(kind: string): "ok" | "warn" | "muted" | "error" {
  if (kind === "hot") return "ok";
  if (kind === "cold") return "muted";
  return "error";
}

function weekdayShort(iso: string): string {
  return new Date(iso + "T00:00:00")
    .toLocaleDateString("pt-BR", { weekday: "short" })
    .replace(/\.$/, "");
}
</script>
