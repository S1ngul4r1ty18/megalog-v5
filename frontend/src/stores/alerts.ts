import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { api, type AlertRow } from "@/lib/api";

// Store leve só para o badge da sidebar (alertas não-reconhecidos).
export const useAlertsStore = defineStore("alerts", () => {
  const items = ref<AlertRow[]>([]);
  const loaded = ref(false);

  async function refresh() {
    try {
      const r = await api.get<AlertRow[]>("/api/admin/alerts", {
        params: { only_unack: true },
      });
      items.value = r.data;
      loaded.value = true;
    } catch {
      // não-admin → 403; ignora
      items.value = [];
      loaded.value = true;
    }
  }

  const unackCount = computed(() => items.value.length);

  return { items, loaded, unackCount, refresh };
});
