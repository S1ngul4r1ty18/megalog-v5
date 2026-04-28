<template>
  <span class="inline-flex items-center gap-1.5">
    <span :class="dotClass" />
    <span class="text-[12px] font-semibold uppercase tracking-wide" :class="textClass">
      <slot>{{ label }}</slot>
    </span>
  </span>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  state: "ok" | "warn" | "error" | "muted";
  pulse?: boolean;
  label?: string;
}>();

const dotClass = computed(() => {
  const base = `inline-block size-2 rounded-full ${props.pulse ? "animate-pulse" : ""}`;
  switch (props.state) {
    case "ok":    return base + " bg-accent-green shadow-[0_0_8px_rgba(34,197,94,0.6)]";
    case "warn":  return base + " bg-accent-amber shadow-[0_0_8px_rgba(245,158,11,0.6)]";
    case "error": return base + " bg-accent-red   shadow-[0_0_8px_rgba(239,68,68,0.6)]";
    default:      return base + " bg-text-dim";
  }
});

const textClass = computed(() => ({
  ok: "text-accent-green", warn: "text-accent-amber", error: "text-accent-red", muted: "text-text-muted",
}[props.state]));
</script>
