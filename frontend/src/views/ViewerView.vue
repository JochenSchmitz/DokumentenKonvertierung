<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api, type DocumentDetail } from '../api'
import { useDocumentsStore } from '../stores/documents'
import { createViewer, loadDocsApi } from '../onlyoffice'

const props = defineProps<{ id: string }>()
const store = useDocumentsStore()

const doc = ref<DocumentDetail | null>(null)
const error = ref('')
// Was zeigt die linke Spalte? PDF -> OnlyOffice-Viewer, Bild -> <img>,
// alles andere (z.B. .msg/.doc/.docx) -> nur Download-Hinweis
const originalKind = ref<'pdf' | 'image' | 'none'>('none')
let editors: { destroyEditor: () => void }[] = []

onMounted(async () => {
  try {
    const [config, detail] = await Promise.all([
      store.loadConfig(),
      api.get(props.id),
    ])
    doc.value = detail
    originalKind.value = detail.mime.startsWith('application/pdf')
      ? 'pdf'
      : detail.mime.startsWith('image/')
        ? 'image'
        : 'none'

    await loadDocsApi(config.onlyofficeUrl)
    const sides: Array<'original' | 'result'> =
      originalKind.value === 'pdf' ? ['original', 'result'] : ['result']
    for (const side of sides) {
      const viewerConfig = await api.onlyofficeConfig(props.id, side)
      // Original: reiner PDF-Viewer ohne Bedienelemente (embedded);
      // Ergebnis: voller Editor mit Bearbeitungswerkzeugen (desktop)
      editors.push(
        createViewer(
          `oo-${side}`,
          viewerConfig,
          side === 'original' ? 'embedded' : 'desktop',
        ),
      )
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
        <span
          v-for="tag in doc.tags"
          :key="tag"
          class="tag"
          :class="{ unreadable: tag === 'Unlesbar' }"
        >{{ tag }}</span>
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
          v-if="originalKind === 'image' && doc"
          :src="`/api/documents/${doc.id}/file/original`"
          :alt="doc.filename"
          class="original-image"
        />
        <p v-else-if="originalKind === 'none' && doc" class="no-preview">
          Für diesen Dateityp gibt es keine Vorschau —
          <a :href="`/api/documents/${doc.id}/file/original`">Original
          herunterladen</a>
        </p>
        <div v-else id="oo-original" class="oo-host"></div>
      </section>
      <section class="pane">
        <header>OCR-Ergebnis (bearbeitbar — Änderungen werden gespeichert)</header>
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
.tag.unreadable {
  background: rgba(220, 38, 38, 0.12);
  color: var(--err);
}
.original-image {
  flex: 1;
  object-fit: contain;
  min-height: 0;
  background: var(--bg-soft);
}
.no-preview {
  color: var(--text-dim);
  text-align: center;
  padding: 2rem 1rem;
}
.error {
  color: var(--err);
}
</style>
