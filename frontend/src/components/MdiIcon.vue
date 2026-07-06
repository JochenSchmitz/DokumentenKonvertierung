<script setup lang="ts">
// Icons kommen projektweit aus Material Design Icons (@mdi/js,
// https://pictogrammers.com/library/mdi/) — Pfaddaten inline, kein CDN.
defineProps<{ path: string; size?: number }>()
</script>

<template>
  <svg
    :width="size ?? 18"
    :height="size ?? 18"
    viewBox="0 0 24 24"
    aria-hidden="true"
  >
    <path :d="path" fill="currentColor" />
  </svg>
</template>

<style scoped>
svg {
  vertical-align: -0.2em;
  flex-shrink: 0;
}
</style>
