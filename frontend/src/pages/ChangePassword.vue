<template>
  <PageHeader title="Trocar senha" subtitle="Mínimo 8 caracteres — Argon2id é usado para hash" />

  <div class="p-6">
    <form @submit.prevent="submit" class="card max-w-md p-6 space-y-4">
      <div>
        <label class="label">Senha atual</label>
        <input v-model="current" type="password" required class="input" autofocus />
      </div>
      <div>
        <label class="label">Nova senha</label>
        <input v-model="next" type="password" required minlength="8" class="input" />
        <div class="text-[11px] text-text-muted mt-1">Use letras, números e símbolos para maior segurança.</div>
      </div>
      <div>
        <label class="label">Confirme</label>
        <input v-model="confirm" type="password" required minlength="8" class="input" />
      </div>
      <div v-if="error" class="text-[12.5px] text-accent-red bg-accent-red/10 border border-accent-red/20 rounded px-3 py-2">
        {{ error }}
      </div>
      <button type="submit" :disabled="loading" class="btn-primary w-full !py-2.5">
        {{ loading ? "Salvando…" : "Salvar nova senha" }}
      </button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import { api } from "@/lib/api";
import { useToastStore } from "@/stores/toast";

const toast = useToastStore();
const current = ref(""); const next = ref(""); const confirm = ref("");
const error = ref(""); const loading = ref(false);

async function submit() {
  error.value = "";
  if (next.value !== confirm.value) { error.value = "As senhas não conferem"; return; }
  loading.value = true;
  try {
    await api.post("/api/auth/change-password", {
      current_password: current.value,
      new_password: next.value,
    });
    toast.success("Senha alterada com sucesso");
    current.value = ""; next.value = ""; confirm.value = "";
  } catch (e: any) {
    error.value = e.response?.data?.detail ?? "Falha ao alterar senha";
  } finally {
    loading.value = false;
  }
}
</script>
