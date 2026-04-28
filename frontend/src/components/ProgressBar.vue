<template>
  <div class="h-1.5 rounded-full bg-white/[.06] overflow-hidden">
    <div
      class="h-full rounded-full transition-all duration-500"
      :class="colorClass"
      :style="{ width: clamped + '%' }"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(defineProps<{
  value: number;             // 0-100
  warnAt?: number;           // amber threshold
  errorAt?: number;          // red threshold
}>(), { warnAt: 70, errorAt: 85 });

const clamped = computed(() => Math.max(0, Math.min(100, props.value)));
const colorClass = computed(() => {
  if (clamped.value >= props.errorAt) return "bg-accent-red";
  if (clamped.value >= props.warnAt)  return "bg-accent-amber";
  return "bg-accent-green";
});
</script>
