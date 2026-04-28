<template>
  <div class="min-h-screen grid place-items-center bg-bg p-6 relative overflow-hidden">
    <!-- background sutil -->
    <div class="absolute inset-0 bg-gradient-radial from-primary/[0.04] via-transparent to-transparent pointer-events-none" />

    <div class="relative card w-full max-w-sm shadow-2xl animate-fade-in">
      <div class="card-body space-y-6 p-8">
        <div class="text-center">
          <div class="mx-auto size-14 rounded-xl bg-gradient-to-br from-primary to-accent-blue grid place-items-center text-bg font-bold text-[18px] mb-4 shadow-glow-primary">
            ML
          </div>
          <h1 class="text-[18px] font-bold text-text-primary tracking-tight">MegaLog</h1>
          <p class="text-[12px] text-text-muted mt-1">Sistema de auditoria CGNAT</p>
        </div>

        <form @submit.prevent="submit" class="space-y-4">
          <div>
            <label class="label">Usuário</label>
            <input v-model="username" type="text" autocomplete="username" required class="input" autofocus />
          </div>
          <div>
            <label class="label">Senha</label>
            <input v-model="password" type="password" autocomplete="current-password" required class="input" />
          </div>

          <div v-if="error" class="text-[12.5px] text-accent-red bg-accent-red/10 border border-accent-red/20 rounded px-3 py-2 animate-fade-in">
            {{ error }}
          </div>

          <button type="submit" :disabled="loading" class="btn-primary w-full !py-2.5">
            {{ loading ? "Entrando…" : "Entrar →" }}
          </button>
        </form>
      </div>
    </div>

    <p class="absolute bottom-4 text-[10px] text-text-dim font-mono">megalog v5</p>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

const username = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");

async function submit() {
  error.value = "";
  loading.value = true;
  try {
    await auth.login(username.value, password.value);
    const next = (route.query.next as string) || "/";
    await router.push(next);
  } catch (e: any) {
    error.value = e.response?.data?.detail ?? "Falha ao autenticar";
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.bg-gradient-radial {
  background-image: radial-gradient(ellipse at center, var(--tw-gradient-stops));
}
</style>
