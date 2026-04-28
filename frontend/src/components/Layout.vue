<template>
  <div class="min-h-screen flex bg-bg">
    <aside class="w-60 shrink-0 bg-bg-card border-r border-border flex flex-col">
      <!-- Brand -->
      <div class="px-5 py-4 border-b border-border">
        <RouterLink to="/" class="flex items-center gap-2.5 group">
          <div class="size-9 rounded-lg bg-gradient-to-br from-primary to-accent-blue grid place-items-center text-bg font-bold text-[14px] shadow-glow-primary">
            ML
          </div>
          <div>
            <div class="font-bold text-[14px] text-text-primary tracking-tight">MegaLog</div>
            <div class="text-[10px] text-text-muted font-mono">v{{ APP_VERSION }}</div>
          </div>
        </RouterLink>
      </div>

      <!-- Nav -->
      <nav class="flex-1 px-2 py-3 space-y-0.5 text-[13px] overflow-y-auto">
        <RouterLink v-for="i in nav" :key="i.to" :to="i.to"
          class="flex items-center justify-between rounded-md px-3 py-2 transition-colors"
          :class="isActive(i) ? 'bg-primary-bg text-primary font-semibold' : 'text-text-secondary hover:bg-white/[.04] hover:text-text-primary'">
          <span class="flex items-center gap-2.5">
            <span class="text-[14px] opacity-70">{{ i.icon }}</span>
            {{ i.label }}
          </span>
          <span v-if="i.badge" class="badge-warn">{{ i.badge }}</span>
        </RouterLink>

        <template v-if="auth.isAdmin()">
          <div class="mt-5 mb-2 px-3 label-micro flex items-center gap-2">
            <span>Admin</span>
            <span class="flex-1 h-px bg-border" />
          </div>
          <RouterLink v-for="i in adminNav" :key="i.to" :to="i.to"
            class="flex items-center justify-between rounded-md px-3 py-2 transition-colors"
            :class="isActive(i) ? 'bg-primary-bg text-primary font-semibold' : 'text-text-secondary hover:bg-white/[.04] hover:text-text-primary'">
            <span class="flex items-center gap-2.5">
              <span class="text-[14px] opacity-70">{{ i.icon }}</span>
              {{ i.label }}
            </span>
            <span v-if="i.badge" class="badge-warn">{{ i.badge }}</span>
          </RouterLink>
        </template>
      </nav>

      <!-- User -->
      <div class="border-t border-border p-3 space-y-1">
        <div class="px-3 py-2 rounded-md bg-bg-elevated">
          <div class="text-[13px] font-semibold text-text-primary truncate">{{ auth.me?.username }}</div>
          <div class="text-[10px] text-text-muted uppercase tracking-wider mt-0.5">{{ auth.me?.role }}</div>
        </div>
        <RouterLink to="/change-password" class="flex items-center gap-2.5 px-3 py-2 rounded-md text-[12.5px] text-text-secondary hover:bg-white/[.04] hover:text-text-primary transition-colors">
          <span class="opacity-70">⚙</span> Trocar senha
        </RouterLink>
        <button @click="auth.logout()" class="w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-[12.5px] text-text-secondary hover:bg-white/[.04] hover:text-text-primary transition-colors">
          <span class="opacity-70">⏻</span> Sair
        </button>
      </div>
    </aside>

    <main class="flex-1 overflow-auto">
      <RouterView />
    </main>

    <Toast />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import Toast from "@/components/Toast.vue";
import { useAuthStore } from "@/stores/auth";
import { useAlertsStore } from "@/stores/alerts";

const APP_VERSION = "5.0.0";
const auth = useAuthStore();
const alerts = useAlertsStore();
const route = useRoute();

interface NavItem {
  to: string;
  label: string;
  icon: string;
  activePrefix?: string;
  badge?: string | number;
}

onMounted(() => { if (auth.isAdmin()) alerts.refresh(); });

const nav = computed<NavItem[]>(() => [
  { to: "/",       label: "Dashboard",    icon: "▤" },
  { to: "/search", label: "Busca forense", icon: "⌕" },
  { to: "/daily",  label: "Logs diários", icon: "▦", activePrefix: "/anomaly/" },
]);

const adminNav = computed<NavItem[]>(() => [
  { to: "/admin/alerts", label: "Alertas",   icon: "⚠", badge: alerts.unackCount > 0 ? alerts.unackCount : "" },
  { to: "/admin/users",  label: "Usuários",  icon: "◉" },
  { to: "/admin/audit",  label: "Auditoria", icon: "≡" },
  { to: "/donate",       label: "Doação",    icon: "♥" },
  { to: "/docs",         label: "Documentação", icon: "📖" },
]);

function isActive(item: NavItem): boolean {
  if (route.path === item.to) return true;
  if (item.activePrefix && route.path.startsWith(item.activePrefix)) return true;
  return false;
}
</script>
