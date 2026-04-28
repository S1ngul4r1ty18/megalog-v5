<template>
  <PageHeader title="Alertas de anomalia" subtitle="Detectados pelo job analyze (timer 02:30)">
    <template #actions>
      <label class="flex items-center gap-2 text-[12.5px] text-text-secondary cursor-pointer">
        <input type="checkbox" v-model="onlyUnack" class="accent-primary" />
        Só pendentes
      </label>
    </template>
  </PageHeader>

  <div class="p-6">
    <div v-if="loading" class="card p-4 space-y-2"><Skeleton v-for="i in 4" :key="i" height="56px" /></div>

    <div v-else-if="filtered.length === 0" class="card">
      <EmptyState
        v-if="onlyUnack"
        title="Nenhum alerta pendente"
        subtitle="Tudo reconhecido. ✓"
        icon="✓"
      />
      <EmptyState
        v-else
        title="Nenhum alerta gerado"
        subtitle="O job analyze é executado às 02:30. Se não houve anomalia significativa em nenhum dia, esta lista fica vazia."
        icon="∅"
      />
    </div>

    <div v-else class="card overflow-hidden">
      <table class="tbl">
        <thead>
          <tr>
            <th>Data</th>
            <th>Classificação</th>
            <th class="text-right">Volume</th>
            <th class="text-right">Razão</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in filtered" :key="a.id" class="group">
            <td class="mono">
              <RouterLink :to="`/anomaly/${a.date}`" class="text-primary hover:underline">{{ a.date }}</RouterLink>
            </td>
            <td class="text-[13px] text-text-primary">{{ a.classification ?? '—' }}</td>
            <td class="num text-text-primary">{{ fmtNumber(a.log_count) }}</td>
            <td class="num">
              <span :class="a.ratio >= 5 ? 'text-accent-red' : 'text-accent-amber'" class="font-semibold">{{ a.ratio.toFixed(1) }}×</span>
            </td>
            <td>
              <span v-if="a.acknowledged" class="badge-ok">reconhecido</span>
              <span v-else class="badge-warn">pendente</span>
            </td>
            <td class="text-right">
              <button
                v-if="!a.acknowledged"
                @click="ack(a.id)"
                class="btn-ghost !py-1 !px-2 opacity-0 group-hover:opacity-100 transition-opacity"
              >Reconhecer</button>
              <span v-else class="text-[11px] text-text-muted">por {{ a.acknowledged_by }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";
import Skeleton from "@/components/Skeleton.vue";
import { api, type AlertRow } from "@/lib/api";
import { fmtNumber } from "@/lib/format";
import { useAlertsStore } from "@/stores/alerts";
import { useToastStore } from "@/stores/toast";

const onlyUnack = ref(false);
const items = ref<AlertRow[]>([]);
const loading = ref(true);
const alertsStore = useAlertsStore();
const toast = useToastStore();

async function load() {
  loading.value = true;
  try { items.value = (await api.get<AlertRow[]>("/api/admin/alerts")).data; }
  finally { loading.value = false; }
}

async function ack(id: number) {
  try {
    await api.post(`/api/admin/alerts/${id}/ack`);
    toast.success("Alerta reconhecido");
    await load();
    await alertsStore.refresh();
  } catch (e: any) {
    toast.error(e.response?.data?.detail ?? "Falha ao reconhecer");
  }
}

const filtered = computed(() =>
  onlyUnack.value ? items.value.filter(a => !a.acknowledged) : items.value,
);

onMounted(load);
</script>
