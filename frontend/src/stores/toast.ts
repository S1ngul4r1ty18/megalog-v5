import { defineStore } from "pinia";
import { ref } from "vue";

export type ToastType = "success" | "error" | "warn" | "info";

export interface ToastItem {
  id: number;
  type: ToastType;
  title?: string;
  message: string;
}

let _id = 0;

export const useToastStore = defineStore("toast", () => {
  const items = ref<ToastItem[]>([]);

  function push(type: ToastType, message: string, opts: { title?: string; ttl?: number } = {}) {
    const id = ++_id;
    items.value.push({ id, type, message, title: opts.title });
    setTimeout(() => dismiss(id), opts.ttl ?? 4000);
  }

  function dismiss(id: number) {
    items.value = items.value.filter(t => t.id !== id);
  }

  return {
    items,
    success: (msg: string, title?: string) => push("success", msg, { title }),
    error:   (msg: string, title?: string) => push("error",   msg, { title, ttl: 6000 }),
    warn:    (msg: string, title?: string) => push("warn",    msg, { title }),
    info:    (msg: string, title?: string) => push("info",    msg, { title }),
    dismiss,
  };
});
