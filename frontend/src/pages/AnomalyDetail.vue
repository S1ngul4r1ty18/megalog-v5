<template>
  <PageHeader :title="`Anomalia · ${dateStr}`" :subtitle="alert?.classification ?? ''">
    <template #actions>
      <RouterLink :to="`/search?date=${dateStr}`" class="btn-secondary">Investigar logs</RouterLink>
      <button v-if="alert && !alert.acknowledged" @click="ack" class="btn-primary">Reconhecer</button>
      <span v-else-if="alert" class="badge-ok">reconhecido por {{ alert.acknowledged_by }}</span>
    </template>
  </PageHeader>

  <div v-if="loading" class="p-6">
    <div class="grid grid-cols-1 sm:grid-cols-4 gap-4 mb-6">
      <Skeleton v-for="i in 4" :key="i" height="92px" />
    </div>
    <Skeleton height="200px" />
  </div>

  <div v-else-if="!alert" class="p-6">
    <EmptyState title="Sem alerta para essa data" subtitle="Pode estar pendente de análise ou não atingiu o threshold de baseline." icon="∅" />
  </div>

  <div v-else class="p-6 space-y-6 animate-fade-in">

    <!-- ── Resumo ───────────────────────────────────────────────────────── -->
    <section class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="card p-4">
        <span class="label-micro">Volume</span>
        <div class="num text-stat-lg font-bold text-text-primary mt-1">{{ fmtNumber(alert.log_count) }}</div>
        <div class="text-[11px] text-text-muted mt-1 num">esperado {{ fmtNumber(alert.expected_count) }}</div>
      </div>

      <div class="card p-4">
        <span class="label-micro">Razão vs baseline</span>
        <div class="num text-stat-lg font-bold mt-1" :class="alert.ratio >= 5 ? 'text-accent-red' : 'text-accent-amber'">
          {{ alert.ratio.toFixed(1) }}<span class="text-[16px] text-text-muted ml-0.5">×</span>
        </div>
        <div class="text-[11px] text-text-muted mt-1">acima do normal histórico</div>
      </div>

      <div class="card p-4">
        <span class="label-micro">Confiança</span>
        <div class="num text-stat-lg font-bold text-primary mt-1">
          {{ ((details?.classification_score ?? 0) * 100).toFixed(0) }}<span class="text-[16px] text-text-muted ml-0.5">%</span>
        </div>
        <div class="text-[11px] text-text-muted mt-1 truncate">{{ details?.classification }}</div>
      </div>

      <div class="card p-4">
        <span class="label-micro">DB do dia</span>
        <div class="num text-stat-lg font-bold text-text-primary mt-1">{{ fmtBytes(alert.db_size_bytes) }}</div>
        <div class="text-[11px] text-text-muted mt-1">criado {{ fmtDateTime(alert.created_at) }}</div>
      </div>
    </section>

    <!-- ── Razões + causas ──────────────────────────────────────────────── -->
    <section class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div class="card">
        <div class="card-header"><span class="card-title">Por que essa classificação</span></div>
        <div class="card-body space-y-2.5">
          <div v-for="(r, i) in details?.classification_reasons" :key="i"
               class="text-[13px] text-text-primary flex gap-2 leading-relaxed">
            <span class="text-primary font-bold mt-0.5">▸</span>
            <span>{{ r }}</span>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><span class="card-title">Possíveis causas</span></div>
        <div class="card-body space-y-2.5">
          <div v-for="(r, i) in details?.possible_causes" :key="i"
               class="text-[13px] text-text-secondary flex gap-2 leading-relaxed">
            <span class="text-text-dim">◦</span>
            <span>{{ r }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- ── Distribuição de portas (gráfico) + protocolos ───────────────── -->
    <section class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div class="card lg:col-span-2">
        <div class="card-header">
          <span class="card-title">Distribuição de portas destino</span>
          <span class="label-micro">{{ fmtNumber(details?.unique_dst_ports) }} portas únicas</span>
        </div>
        <div class="card-body">
          <VChart :option="portsChartOption" autoresize style="height: 260px;" />
        </div>
      </div>

      <div class="card">
        <div class="card-header"><span class="card-title">Protocolos destacados</span></div>
        <div class="card-body space-y-3">
          <div v-for="p in protoRows" :key="p.label" class="flex items-center justify-between">
            <span class="text-[12.5px] text-text-secondary uppercase tracking-wide">{{ p.label }}</span>
            <div class="text-right">
              <div class="num text-[15px] font-semibold text-text-primary">{{ fmtNumber(p.count) }}</div>
              <div class="num text-[11px] text-text-muted">{{ (p.pct * 100).toFixed(1) }}%</div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ── Top tabelas ─────────────────────────────────────────────────── -->
    <section class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div class="card overflow-hidden">
        <div class="card-header"><span class="card-title">Top IPs origem</span></div>
        <table class="tbl">
          <thead><tr><th>IP</th><th class="text-right">Conexões</th><th class="text-right">Portas</th><th class="text-right">% BK</th></tr></thead>
          <tbody>
            <tr v-for="ip in details?.top_ips" :key="ip.ip">
              <td class="mono text-text-primary">{{ ip.ip }}</td>
              <td class="num">{{ fmtNumber(ip.connections) }}</td>
              <td class="num">{{ fmtNumber(ip.dst_ports) }}</td>
              <td class="num">{{ ip.well_known_pct.toFixed(0) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card overflow-hidden">
        <div class="card-header"><span class="card-title">Top portas destino</span></div>
        <table class="tbl">
          <thead><tr><th>Porta</th><th>Serviço</th><th class="text-right">Conexões</th></tr></thead>
          <tbody>
            <tr v-for="p in details?.top_dst_ports" :key="p.port">
              <td class="mono text-text-primary">{{ p.port }}</td>
              <td class="text-[12px] text-text-secondary">{{ p.service || '—' }}</td>
              <td class="num">{{ fmtNumber(p.count) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card overflow-hidden">
        <div class="card-header"><span class="card-title">Top IPs destino</span></div>
        <table class="tbl">
          <thead><tr><th>IP</th><th class="text-right">Conexões</th><th class="text-right">%</th></tr></thead>
          <tbody>
            <tr v-for="ip in details?.top_dst_ips" :key="ip.ip">
              <td class="mono text-text-primary">{{ ip.ip }}</td>
              <td class="num">{{ fmtNumber(ip.count) }}</td>
              <td class="num text-text-muted">{{ ip.pct.toFixed(2) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ── Análise textual ──────────────────────────────────────────────── -->
    <section class="card">
      <div class="card-header">
        <span class="card-title">Análise narrativa completa</span>
        <span class="label-micro">gerada por classifier multi-sinal</span>
      </div>
      <div class="card-body">
        <pre class="font-mono text-[12px] text-text-secondary whitespace-pre-wrap leading-relaxed">{{ alert.analysis }}</pre>
      </div>
    </section>

  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { use as echartsUse } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import VChart from "vue-echarts";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";
import Skeleton from "@/components/Skeleton.vue";
import { api, type AlertRow, type AnomalyDetails } from "@/lib/api";
import { fmtBytes, fmtDateTime, fmtNumber } from "@/lib/format";
import { useToastStore } from "@/stores/toast";

echartsUse([CanvasRenderer, BarChart, GridComponent, TooltipComponent]);

const route = useRoute();
const dateStr = computed(() => route.params.date as string);
const loading = ref(true);
const alert = ref<AlertRow | null>(null);
const toast = useToastStore();

const details = computed<AnomalyDetails | null>(() => {
  if (!alert.value?.details_json) return null;
  try { return JSON.parse(alert.value.details_json); } catch { return null; }
});

const protoRows = computed(() => {
  const p = details.value?.protocols;
  if (!p) return [];
  return [
    { label: "DNS",     count: p.dns_conns,   pct: p.dns_pct },
    { label: "HTTP/S",  count: p.http_conns,  pct: p.http_pct },
    { label: "NTP",     count: p.ntp_conns,   pct: p.ntp_pct },
    { label: "SSH",     count: p.ssh_conns,   pct: p.ssh_pct },
  ].filter(r => r.count > 0);
});

async function load() {
  loading.value = true;
  try {
    const list = await api.get<AlertRow[]>("/api/admin/alerts");
    alert.value = list.data.find(a => a.date === dateStr.value) ?? null;
  } finally {
    loading.value = false;
  }
}

async function ack() {
  if (!alert.value) return;
  try {
    await api.post(`/api/admin/alerts/${alert.value.id}/ack`);
    toast.success("Alerta reconhecido", "OK");
    await load();
  } catch (e: any) {
    toast.error(e.response?.data?.detail ?? "Falha ao reconhecer");
  }
}

const portsChartOption = computed(() => {
  const d = details.value;
  if (!d) return {};
  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: "#1d2030",
      borderColor: "#2c303a",
      textStyle: { color: "#ffffff", fontFamily: "Montserrat, sans-serif", fontSize: 12 },
    },
    grid: { left: 50, right: 20, top: 16, bottom: 30 },
    xAxis: {
      type: "category",
      data: ["bem conhecidas\n0–1023", "registradas\n1024–49151", "efêmeras\n49152+"],
      axisLine:  { lineStyle: { color: "#2c303a" } },
      axisLabel: { color: "#8b95a7", fontSize: 11, lineHeight: 14 },
    },
    yAxis: {
      type: "value",
      axisLine:  { show: false },
      axisLabel: { color: "#5b6478", formatter: (v: number) => v + "%" },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.04)" } },
    },
    series: [{
      type: "bar",
      data: [
        { value: d.port_ranges.well_known_pct * 100, itemStyle: { color: "#22c55e" } },
        { value: d.port_ranges.registered_pct * 100, itemStyle: { color: "#22d3ee" } },
        { value: d.port_ranges.ephemeral_pct  * 100, itemStyle: { color: "#a78bfa" } },
      ],
      barWidth: "40%",
      itemStyle: { borderRadius: [4, 4, 0, 0] },
      label: {
        show: true,
        position: "top",
        formatter: (p: any) => `${p.value.toFixed(1)}%`,
        color: "#e6edf3",
        fontFamily: "JetBrains Mono, monospace",
        fontSize: 12,
      },
    }],
  };
});

onMounted(load);
</script>
