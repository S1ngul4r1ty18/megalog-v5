<template>
  <PageHeader title="Busca forense" subtitle="Filtre por data, IPs, portas, protocolo e janela de tempo">
    <template #actions>
      <button @click="exportCsv" :disabled="!result || loading" class="btn-secondary">↓ Exportar CSV</button>
    </template>
  </PageHeader>

  <div class="p-6 space-y-5 animate-fade-in">

    <!-- Filtros -->
    <form @submit.prevent="run(1)" class="card p-4">
      <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <div>
          <label class="label">Data</label>
          <input type="date" v-model="q.date" required class="input" />
        </div>
        <div>
          <label class="label">IP origem (privado)</label>
          <input v-model="q.src_ip" placeholder="100.80.0.119" class="input font-mono" />
        </div>
        <div>
          <label class="label">IP destino</label>
          <input v-model="q.dst_ip" placeholder="8.8.8.8" class="input font-mono" />
        </div>
        <div>
          <label class="label">IP NAT (público)</label>
          <input v-model="q.nat_ip" placeholder="170.245.x.y" class="input font-mono" />
        </div>
        <div>
          <label class="label">Porta destino</label>
          <input v-model.number="q.dst_port" type="number" min="0" max="65535" class="input" />
        </div>
        <div>
          <label class="label">Porta NAT</label>
          <input v-model.number="q.nat_port" type="number" min="0" max="65535" class="input" />
        </div>
        <div>
          <label class="label">Protocolo</label>
          <select v-model="q.proto" class="input">
            <option value="">qualquer</option>
            <option value="TCP">TCP</option>
            <option value="UDP">UDP</option>
          </select>
        </div>
        <div>
          <label class="label">Hora inicial</label>
          <input v-model="tsStartStr" type="time" step="1" class="input" />
        </div>
        <div>
          <label class="label">Hora final</label>
          <input v-model="tsEndStr" type="time" step="1" class="input" />
        </div>
        <div>
          <label class="label">Por página</label>
          <select v-model.number="q.per_page" class="input">
            <option :value="50">50</option>
            <option :value="100">100</option>
            <option :value="500">500</option>
            <option :value="1000">1000</option>
          </select>
        </div>
        <div class="col-span-2 md:col-span-1 lg:col-span-2 flex items-end gap-2">
          <button type="submit" :disabled="loading" class="btn-primary flex-1">
            {{ loading ? "Buscando…" : "Buscar" }}
          </button>
          <button type="button" @click="reset" class="btn-secondary">Limpar</button>
        </div>
      </div>
    </form>

    <!-- Erro -->
    <div v-if="error" class="card border-l-2 !border-l-accent-red px-4 py-3 text-[13px] text-accent-red">{{ error }}</div>

    <!-- Resultados -->
    <div v-if="loading && !result" class="card p-4 space-y-2"><Skeleton v-for="i in 8" :key="i" height="24px" /></div>

    <div v-if="result" class="card overflow-hidden">
      <div class="card-header">
        <span class="card-title">
          <span class="num text-text-primary">{{ fmtNumber(result.total) }}</span>
          <span class="text-text-secondary font-normal"> resultado(s)</span>
          <span v-if="result.total > 0" class="text-text-muted font-normal"> · página {{ result.page }} de {{ totalPages }}</span>
        </span>
        <div class="flex items-center gap-1">
          <button class="btn-ghost !px-2 !py-1" :disabled="result.page <= 1"            @click="run(result.page - 1)">‹</button>
          <span class="text-[12px] text-text-muted px-2 num">{{ result.page }} / {{ totalPages }}</span>
          <button class="btn-ghost !px-2 !py-1" :disabled="result.page >= totalPages"   @click="run(result.page + 1)">›</button>
        </div>
      </div>

      <div v-if="result.rows.length === 0" class="py-6">
        <EmptyState title="Nenhum log encontrado" subtitle="Ajuste os filtros (IPs, portas, janela de tempo) e tente novamente." />
      </div>

      <div v-else class="overflow-auto max-h-[65vh]">
        <table class="tbl tbl-compact">
          <thead>
            <tr>
              <th class="w-[88px]">Hora</th>
              <th>Origem (privado)</th>
              <th>Destino</th>
              <th class="!text-primary">NAT (público)</th>
              <th class="w-[64px]">Proto</th>
              <th class="text-right w-[72px]">Bytes</th>
              <th>Iface in → out</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, idx) in result.rows" :key="idx">
              <td class="mono whitespace-nowrap">{{ new Date(r.ts * 1000).toLocaleTimeString("pt-BR") }}</td>
              <td class="mono text-text-primary whitespace-nowrap">
                {{ r.src_ip }}<span class="text-text-muted">:{{ r.src_port }}</span>
              </td>
              <td class="mono text-text-secondary whitespace-nowrap">
                {{ r.dst_ip }}<span class="text-text-muted">:{{ r.dst_port }}</span>
              </td>
              <td class="mono text-primary whitespace-nowrap">
                {{ r.nat_ip }}<span class="text-primary/60">:{{ r.nat_port }}</span>
              </td>
              <td><span class="badge-muted">{{ r.proto }}</span></td>
              <td class="num">{{ r.pkt_len }}</td>
              <td class="text-[11.5px] text-text-muted whitespace-nowrap">
                {{ r.in_iface }} <span class="text-text-dim">→</span> {{ r.out_iface }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";
import Skeleton from "@/components/Skeleton.vue";
import { api, type SearchQuery, type SearchResult } from "@/lib/api";
import { fmtNumber } from "@/lib/format";
import { useToastStore } from "@/stores/toast";

const route = useRoute();
const toast = useToastStore();

function todayIso(): string { return new Date().toISOString().slice(0, 10); }

const q = reactive<SearchQuery>({
  date: (route.query.date as string) || todayIso(),
  src_ip: "", dst_ip: "", nat_ip: "",
  src_port: undefined, dst_port: undefined, nat_port: undefined,
  proto: undefined, ts_start: undefined, ts_end: undefined,
  page: 1, per_page: 100,
});
const tsStartStr = ref("");
const tsEndStr = ref("");

const loading = ref(false);
const result = ref<SearchResult | null>(null);
const error = ref("");

const totalPages = computed(() =>
  result.value ? Math.max(1, Math.ceil(result.value.total / (result.value.per_page || 100))) : 1,
);

function buildPayload(): SearchQuery {
  const payload: any = { ...q, page: q.page, per_page: q.per_page };
  for (const k of Object.keys(payload)) {
    if (payload[k] === "" || payload[k] === null || payload[k] === undefined) delete payload[k];
  }
  if (tsStartStr.value) payload.ts_start = timeOnDateToTs(q.date!, tsStartStr.value);
  if (tsEndStr.value)   payload.ts_end   = timeOnDateToTs(q.date!, tsEndStr.value);
  return payload;
}

function timeOnDateToTs(dateIso: string, time: string): number {
  return Math.floor(new Date(`${dateIso}T${time}`).getTime() / 1000);
}

async function run(page: number) {
  q.page = page;
  loading.value = true;
  error.value = "";
  try {
    const r = await api.post<SearchResult>("/api/search", buildPayload());
    result.value = r.data;
  } catch (e: any) {
    error.value = e.response?.data?.detail ?? "Falha na busca";
    result.value = null;
  } finally {
    loading.value = false;
  }
}

async function exportCsv() {
  try {
    const r = await api.post("/api/search/export", buildPayload(), { responseType: "blob" });
    const url = URL.createObjectURL(r.data);
    const a = document.createElement("a");
    a.href = url; a.download = `megalog-${q.date}.csv`; a.click();
    URL.revokeObjectURL(url);
    toast.success("CSV exportado");
  } catch (e: any) {
    toast.error(e.response?.data?.detail ?? "Falha ao exportar");
  }
}

function reset() {
  Object.assign(q, {
    date: todayIso(), src_ip: "", dst_ip: "", nat_ip: "",
    src_port: undefined, dst_port: undefined, nat_port: undefined,
    proto: undefined, ts_start: undefined, ts_end: undefined,
    page: 1, per_page: 100,
  });
  tsStartStr.value = ""; tsEndStr.value = "";
  result.value = null;
}

watch(() => route.query.date, (nd) => { if (nd && nd !== q.date) q.date = nd as string; });
onMounted(() => { if (route.query.date) run(1); });
</script>
