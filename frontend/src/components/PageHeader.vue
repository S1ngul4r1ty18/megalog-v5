<template>
  <div class="px-6 py-4 border-b border-border bg-bg-card/50 backdrop-blur-sm sticky top-0 z-20">
    <div class="flex items-center justify-between gap-4 flex-wrap">
      <div>
        <h1 class="text-[18px] font-semibold text-text-primary tracking-tight">{{ title }}</h1>
        <p v-if="subtitle" class="text-[12.5px] text-text-secondary mt-0.5">{{ subtitle }}</p>
      </div>
      <div class="flex items-center gap-2 flex-wrap"><slot name="actions" /></div>
    </div>
    <slot />
  </div>
</template>

<script setup lang="ts">
defineProps<{ title: string; subtitle?: string }>();
</script>
