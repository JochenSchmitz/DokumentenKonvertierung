<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api, type DocumentDetail } from '../api'
import { useDocumentsStore } from '../stores/documents'
import { createViewer, loadDocsApi } from '../onlyoffice'

const props = defineProps<{ id: string }>()
const store = useDocumentsStore()

const doc = ref<DocumentDetail | null>(null)
const error = ref('')
const isImage = ref(false)
let editors: { destroyEditor: () => void }[] = []

onMounted(async () => {
  try {
    const [config, detail] = await Promise.all([
      store.loadConfig(),
      api.get(props.id),
    ])
    doc.value = detail
    isImage.value = !detail.mime.startsWith('application/pdf')

    await loadDocsApi(config.onlyofficeUrl)
    const sides: Array<'original' | 'result'> = isImage.value
      ? ['result']
      : ['original', 'result']
    for (const side of sides) {
      const viewerConfig = await api.onlyofficeConfig(props.id, side)
      editors.push(createViewer(`oo-${side}`, viewerConfig))
    }
  } catch (e) {
    error.value = (e as Error).message
  }
})

onBeforeUnmount(() => {
  editors.forEach((e) => e.destroyEditor())
  editors = []
})
</script>

<template>
  <div class="viewer">
    <div class="head">
      <h2>{{ doc?.filename ?? '…' }}</h2>
      <div v-if="doc" class="meta">
        <span v-for="tag in doc.tags" :key="tag" class="tag">{{ tag }}</span>
      </div>
      <a v-if="doc" :href="`/api/documents/${doc.id}/file/docx`">
        <button class="primary">.docx herunterladen</button>
      </a>
    </div>
    <p v-if="doc?.summary" class="summary">{{ doc.summary }}</p>
    <p v-if="error" class="error">{{ error }}</p>

    <div class="panes">
      <section class="pane">
        <header>Original</header>
        <img
          v-if="isImage && doc"
          :src="`/api/documents/${doc.id}/file/original`"
          :alt="doc.filename"
          class="original-image"
        />
        <div v-else id="oo-original" class="oo-host"></div>
      </section>
      <section class="pane">
        <header>OCR-Ergebnis</header>
        <div id="oo-result" class="oo-host"></div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.viewer {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 0.8rem 1.2rem;
  gap: 0.5rem;
}
.head {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}
.head h2 {
  margin: 0;
  font-size: 1.05rem;
}
.summary {
  margin: 0;
  color: var(--text-dim);
  font-size: 0.88rem;
}
.tag {
  display: inline-block;
  background: var(--accent-bg);
  color: var(--accent);
  border-radius: 999px;
  padding: 0.05rem 0.55rem;
  font-size: 0.78rem;
  margin-right: 0.25rem;
  white-space: nowrap;
}
.panes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.8rem;
  flex: 1;
  min-height: 0;
}
.pane {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  min-height: 0;
}
.pane header {
  padding: 0.3rem 0.8rem;
  background: var(--bg-soft);
  border-bottom: 1px solid var(--border);
  font-weight: 600;
  font-size: 0.85rem;
}
.oo-host {
  flex: 1;
  min-height: 0;
}
.original-image {
  flex: 1;
  object-fit: contain;
  min-height: 0;
  background: var(--bg-soft);
}
.error {
  color: var(--err);
}
</style>
