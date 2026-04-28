<template>
  <div class="skeleton" :style="{ width, height }" />
</template>

<script setup lang="ts">
withDefaults(defineProps<{ width?: string; height?: string }>(), {
  width: "100%",
  height: "1em",
});
</script>
