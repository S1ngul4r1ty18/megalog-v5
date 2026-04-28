<template>
  <div class="text-center py-12 px-6">
    <div class="mx-auto size-12 rounded-xl bg-white/[.04] grid place-items-center mb-3">
      <span class="text-text-dim text-2xl">{{ icon }}</span>
    </div>
    <h3 class="text-[14px] font-semibold text-text-primary">{{ title }}</h3>
    <p v-if="subtitle" class="text-[12.5px] text-text-secondary mt-1 max-w-sm mx-auto">{{ subtitle }}</p>
    <div v-if="$slots.action" class="mt-4">
      <slot name="action" />
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  title: string;
  subtitle?: string;
  icon?: string;
}>(), { icon: "∅" });
</script>
