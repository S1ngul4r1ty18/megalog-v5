<template>
  <Teleport to="body">
    <div class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      <transition-group name="toast">
        <div
          v-for="t in store.items"
          :key="t.id"
          class="card pointer-events-auto px-4 py-3 min-w-[240px] max-w-md flex items-start gap-3 animate-fade-in shadow-xl"
          :class="borderClass(t.type)"
        >
          <span :class="iconClass(t.type)">{{ icon(t.type) }}</span>
          <div class="flex-1 text-[12.5px]">
            <div v-if="t.title" class="font-semibold text-text-primary">{{ t.title }}</div>
            <div class="text-text-secondary">{{ t.message }}</div>
          </div>
          <button @click="store.dismiss(t.id)" class="text-text-dim hover:text-text-primary text-lg leading-none">×</button>
        </div>
      </transition-group>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { useToastStore } from "@/stores/toast";

const store = useToastStore();

function borderClass(type: string) {
  return {
    success: "border-l-2 border-l-accent-green",
    error:   "border-l-2 border-l-accent-red",
    warn:    "border-l-2 border-l-accent-amber",
    info:    "border-l-2 border-l-primary",
  }[type] ?? "";
}
function iconClass(type: string) {
  return {
    success: "text-accent-green",
    error:   "text-accent-red",
    warn:    "text-accent-amber",
    info:    "text-primary",
  }[type] ?? "text-text-secondary";
}
function icon(type: string) {
  return { success: "✓", error: "✕", warn: "!", info: "i" }[type] ?? "•";
}
</script>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all 0.25s ease; }
.toast-enter-from { opacity: 0; transform: translateX(20px); }
.toast-leave-to   { opacity: 0; transform: translateX(20px); }
</style>
