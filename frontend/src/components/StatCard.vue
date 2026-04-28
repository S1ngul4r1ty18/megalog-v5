<template>
  <div class="card p-4 group" :class="{ 'card-hover': hover !== false }">
    <div class="flex items-start justify-between gap-2 mb-3">
      <span class="label-micro">{{ label }}</span>
      <span v-if="value !== undefined" class="num text-stat font-bold text-text-primary">
        {{ value }}<span v-if="unit" class="text-text-muted text-[14px] font-semibold ml-0.5">{{ unit }}</span>
      </span>
      <slot v-else name="value" />
    </div>
    <div class="text-[11.5px] text-text-secondary">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  label: string;
  value?: string | number;
  unit?: string;
  hover?: boolean;
}>();
</script>
