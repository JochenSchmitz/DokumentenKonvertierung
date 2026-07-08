<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { mdiArrowLeft } from '@mdi/js'
import MdiIcon from '../components/MdiIcon.vue'
import { api, type DocumentDetail } from '../api'
import { useDocumentsStore } from '../stores/documents'
import { createViewer, loadDocsApi } from '../onlyoffice'

const props = defineProps<{ id: string }>()
const store = useDocumentsStore()
const router = useRouter()

const doc = ref<DocumentDetail | null>(null)
const error = ref('')
// Was zeigt die linke Spalte? PDF -> OnlyOffice-Viewer, Bild -> <img>,
// alles andere (z.B. .msg/.doc/.docx) -> nur Download-Hinweis
const originalKind = ref<'pdf' | 'image' | 'none'>('none')
let editors: { destroyEditor: () => void }[] = []

// Breite der linken Spalte (Original) in Prozent; per Splitter ziehbar.
const leftWidth = ref(50)
const panesEl = ref<HTMLElement | null>(null)
const dragging = ref(false)

function onDragStart(e: PointerEvent) {
  dragging.value = true
  // Pointer-Capture: Move/Up erreichen den Splitter auch über den iframes.
  ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
}

function onDragMove(e: PointerEvent) {
  if (!dragging.value || !panesEl.value) return
  const rect = panesEl.value.getBoundingClientRect()
  const pct = ((e.clientX - rect.left) / rect.width) * 100
  leftWidth.value = Math.min(80, Math.max(20, pct))
}

function onDragEnd(e: PointerEvent) {
  dragging.value = false
  ;(e.target as HTMLElement).releasePointerCapture(e.pointerId)
}

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
      <button class="navbtn" @click="router.push({ name: 'upload' })">
        <MdiIcon :path="mdiArrowLeft" :size="16" /> Übersicht
      </button>
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

    <div ref="panesEl" class="panes" :class="{ dragging }">
      <section class="pane" :style="{ width: leftWidth + '%' }">
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

      <div
        class="splitter"
        title="Ziehen zum Verschieben"
        @pointerdown="onDragStart"
        @pointermove="onDragMove"
        @pointerup="onDragEnd"
      />

      <section class="pane result">
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
.navbtn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
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
  display: flex;
  flex: 1;
  min-height: 0;
}
/* Während des Ziehens: iframes schlucken keine Zeigerereignisse mehr */
.panes.dragging .pane {
  pointer-events: none;
}
.pane {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  min-height: 0;
  min-width: 0;
}
.pane.result {
  flex: 1;
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
.splitter {
  flex: 0 0 10px;
  cursor: col-resize;
  position: relative;
}
.splitter::before {
  content: '';
  position: absolute;
  inset: 0 4px;
  background: var(--border);
  border-radius: 2px;
}
.splitter:hover::before,
.panes.dragging .splitter::before {
  background: var(--accent);
}
.error {
  color: var(--err);
}
</style>
