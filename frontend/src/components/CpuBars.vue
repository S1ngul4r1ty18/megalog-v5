<template>
  <div class="flex items-end gap-[3px] h-10">
    <div
      v-for="(p, i) in cores"
      :key="i"
      class="flex flex-col items-center justify-end flex-1 h-full gap-0.5"
      :title="`Core ${i}: ${p.toFixed(0)}%`"
    >
      <div
        class="w-full rounded-t-sm min-h-[3px] transition-all duration-500"
        :class="colorFor(p)"
        :style="{ height: Math.max(4, p) + '%' }"
      />
      <span v-if="cores.length <= 16" class="text-[8px] text-text-dim leading-none num">{{ i }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ cores: number[] }>();

function colorFor(p: number): string {
  if (p >= 80) return "bg-accent-red";
  if (p >= 50) return "bg-accent-amber";
  return "bg-accent-green";
}
</script>
