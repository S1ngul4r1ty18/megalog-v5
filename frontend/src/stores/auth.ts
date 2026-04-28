import { defineStore } from "pinia";
import { ref } from "vue";
import { api, type Me } from "@/lib/api";

export const useAuthStore = defineStore("auth", () => {
  const me = ref<Me | null>(null);
  const ready = ref(false);

  async function bootstrap() {
    try {
      const r = await api.get<Me>("/api/auth/me");
      me.value = r.data;
    } catch {
      me.value = null;
    } finally {
      ready.value = true;
    }
  }

  async function login(username: string, password: string) {
    await api.post("/api/auth/login", { username, password });
    await bootstrap();
  }

  async function logout() {
    try { await api.post("/api/auth/logout"); } catch { /* ignore */ }
    me.value = null;
    window.location.href = "/login";
  }

  function isAdmin() { return me.value?.role === "admin"; }

  return { me, ready, bootstrap, login, logout, isAdmin };
});
