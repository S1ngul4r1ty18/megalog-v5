<template>
  <PageHeader title="Usuários" subtitle="Criar, alterar papel, resetar senha ou desativar">
    <template #actions>
      <button @click="showCreate = true" class="btn-primary">+ Novo usuário</button>
    </template>
  </PageHeader>

  <div class="p-6">
    <div v-if="loading" class="card p-4 space-y-2"><Skeleton v-for="i in 4" :key="i" height="40px" /></div>

    <div v-else-if="users.length === 0" class="card">
      <EmptyState title="Sem usuários" />
    </div>

    <div v-else class="card overflow-hidden">
      <table class="tbl">
        <thead>
          <tr><th>Usuário</th><th>Papel</th><th>Criado</th><th>Último login</th><th>Status</th><th class="text-right">Ações</th></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" class="group">
            <td class="mono">
              <span class="text-text-primary">{{ u.username }}</span>
              <span v-if="u.id === auth.me?.user_id" class="badge-info ml-2">você</span>
            </td>
            <td>
              <select :value="u.role" @change="changeRole(u, $event)" class="input !w-24 !py-1 !text-[12px]">
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </td>
            <td class="mono text-text-muted">{{ fmtDateTime(u.created_at) }}</td>
            <td class="mono text-text-muted">{{ fmtDateTime(u.last_login) }}</td>
            <td>
              <span :class="u.active ? 'badge-ok' : 'badge-muted'">{{ u.active ? 'ativo' : 'inativo' }}</span>
            </td>
            <td class="text-right">
              <div class="flex justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button @click="resetPwd(u)" class="btn-ghost !py-1 !px-2 text-[11.5px]">resetar senha</button>
                <button v-if="u.id !== auth.me?.user_id" @click="deactivate(u)" class="btn-danger !py-1 !px-2 text-[11.5px]">desativar</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Modal de criação -->
    <Teleport to="body">
      <div v-if="showCreate" class="fixed inset-0 bg-black/70 backdrop-blur-sm grid place-items-center z-50 animate-fade-in" @click.self="showCreate = false">
        <div class="card w-full max-w-sm shadow-2xl">
          <div class="card-header">
            <span class="card-title">Novo usuário</span>
            <button @click="showCreate = false" class="text-text-muted hover:text-text-primary text-xl leading-none">×</button>
          </div>
          <form @submit.prevent="createUser" class="card-body space-y-3">
            <div>
              <label class="label">Usuário</label>
              <input v-model="form.username" required minlength="2" class="input" autofocus />
            </div>
            <div>
              <label class="label">Senha (mín. 8 chars)</label>
              <input v-model="form.password" type="password" required minlength="8" class="input" />
            </div>
            <div>
              <label class="label">Papel</label>
              <select v-model="form.role" class="input">
                <option value="user">user — acesso a busca/dashboard</option>
                <option value="admin">admin — gerencia usuários e alertas</option>
              </select>
            </div>
            <div class="flex justify-end gap-2 pt-2">
              <button type="button" @click="showCreate = false" class="btn-secondary">Cancelar</button>
              <button type="submit" class="btn-primary">Criar usuário</button>
            </div>
          </form>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";
import Skeleton from "@/components/Skeleton.vue";
import { api, type UserRow } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";

const auth = useAuthStore();
const toast = useToastStore();
const users = ref<UserRow[]>([]);
const loading = ref(true);
const showCreate = ref(false);
const form = reactive({ username: "", password: "", role: "user" as "user" | "admin" });

async function load() {
  loading.value = true;
  try { users.value = (await api.get<UserRow[]>("/api/admin/users")).data; }
  finally { loading.value = false; }
}

async function createUser() {
  try {
    await api.post("/api/admin/users", { ...form });
    toast.success(`Usuário ${form.username} criado`);
    showCreate.value = false;
    Object.assign(form, { username: "", password: "", role: "user" });
    await load();
  } catch (e: any) {
    toast.error(e.response?.data?.detail ?? "Falha ao criar usuário");
  }
}

async function changeRole(u: UserRow, ev: Event) {
  const role = (ev.target as HTMLSelectElement).value;
  try {
    await api.put(`/api/admin/users/${u.id}/role`, { role });
    toast.success(`${u.username} agora é ${role}`);
    await load();
  } catch (e: any) {
    toast.error(e.response?.data?.detail ?? "Falha");
  }
}

async function resetPwd(u: UserRow) {
  const np = prompt(`Nova senha para ${u.username} (mínimo 8 chars):`);
  if (!np) return;
  if (np.length < 8) { toast.error("Senha precisa ter pelo menos 8 caracteres"); return; }
  try {
    await api.post(`/api/admin/users/${u.id}/reset-password`, { new_password: np });
    toast.success(`Senha de ${u.username} redefinida`);
  } catch (e: any) {
    toast.error(e.response?.data?.detail ?? "Falha");
  }
}

async function deactivate(u: UserRow) {
  if (!confirm(`Desativar usuário ${u.username}? Ele não conseguirá mais entrar.`)) return;
  try {
    await api.delete(`/api/admin/users/${u.id}`);
    toast.success(`${u.username} desativado`);
    await load();
  } catch (e: any) {
    toast.error(e.response?.data?.detail ?? "Falha");
  }
}

onMounted(load);
</script>
