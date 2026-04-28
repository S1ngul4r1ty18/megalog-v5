<template>
  <PageHeader title="Auditoria" subtitle="Histórico imutável de ações de todos os usuários">
    <template #actions>
      <select v-model="actionFilter" @change="load" class="input !w-44">
        <option value="">todas as ações</option>
        <option v-for="a in distinctActions" :key="a" :value="a">{{ a }}</option>
      </select>
    </template>
  </PageHeader>

  <div class="p-6">
    <div v-if="loading" class="card p-4 space-y-2"><Skeleton v-for="i in 6" :key="i" height="32px" /></div>
    <div v-else-if="rows.length === 0" class="card">
      <EmptyState title="Sem entradas" subtitle="Nenhuma ação foi auditada com este filtro." />
    </div>
    <div v-else class="card overflow-hidden">
      <div class="card-header">
        <span class="card-title">{{ rows.length }} eventos</span>
        <span class="label-micro">mais recentes primeiro</span>
      </div>
      <div class="overflow-y-auto max-h-[75vh]">
        <table class="tbl">
          <thead>
            <tr><th>Quando</th><th>Usuário</th><th>Ação</th><th>Detalhes</th><th>IP</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.id">
              <td class="mono">{{ fmtDateTime(r.ts) }}</td>
              <td>
                <span v-if="r.username" class="text-text-primary">{{ r.username }}</span>
                <span v-else class="text-text-dim">—</span>
              </td>
              <td><span :class="actionBadge(r.action)">{{ r.action }}</span></td>
              <td class="text-[12px] text-text-secondary truncate max-w-md font-mono">{{ r.details ?? '—' }}</td>
              <td class="mono text-text-muted">{{ r.ip_address ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";
import Skeleton from "@/components/Skeleton.vue";
import { api, type AuditRow } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";

const rows = ref<AuditRow[]>([]);
const actionFilter = ref("");
const loading = ref(true);

async function load() {
  loading.value = true;
  try {
    const params: any = { limit: 500 };
    if (actionFilter.value) params.action = actionFilter.value;
    rows.value = (await api.get<AuditRow[]>("/api/admin/audit", { params })).data;
  } finally { loading.value = false; }
}

const distinctActions = computed(() =>
  Array.from(new Set(rows.value.map(r => r.action))).sort(),
);

watch(actionFilter, load);
onMounted(load);

function actionBadge(action: string): string {
  if (action.startsWith("login_failed") || action.includes("failed")) return "badge-error";
  if (action === "login" || action === "logout") return "badge-info";
  if (action.startsWith("create") || action.startsWith("update") || action.startsWith("reset")) return "badge-warn";
  if (action === "ack_alert") return "badge-ok";
  return "badge-muted";
}
</script>
