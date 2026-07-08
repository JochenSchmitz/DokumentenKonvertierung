<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  mdiAccountMultipleOutline,
  mdiArrowLeft,
  mdiClose,
  mdiDownload,
  mdiPlus,
  mdiTagOutline,
} from '@mdi/js'
import MdiIcon from '../components/MdiIcon.vue'
import { api, type DocumentDetail, type DocumentEntity } from '../api'
import { useDocumentsStore } from '../stores/documents'
import { createViewer, loadDocsApi } from '../onlyoffice'
import { fmtDate, fmtDateTime, stem } from '../docsort'

const props = defineProps<{ id: string }>()
const store = useDocumentsStore()
const router = useRouter()

const doc = ref<DocumentDetail | null>(null)
const error = ref('')
let editor: { destroyEditor: () => void } | null = null

// Breite der linken Metadaten-Spalte in Prozent; per Splitter ziehbar.
const leftWidth = ref(25)
const splitEl = ref<HTMLElement | null>(null)
const dragging = ref(false)

function onDragStart(e: PointerEvent) {
  dragging.value = true
  // Pointer-Capture: Move/Up erreichen den Splitter auch, wenn der
  // Zeiger über den OnlyOffice-iframe wandert.
  ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
}

function onDragMove(e: PointerEvent) {
  if (!dragging.value || !splitEl.value) return
  const rect = splitEl.value.getBoundingClientRect()
  const pct = ((e.clientX - rect.left) / rect.width) * 100
  leftWidth.value = Math.min(60, Math.max(15, pct))
}

function onDragEnd(e: PointerEvent) {
  dragging.value = false
  ;(e.target as HTMLElement).releasePointerCapture(e.pointerId)
}

// Tag-Pflege: jede Änderung wird sofort per PATCH gespeichert
// (optimistisch, mit Rücksetzen bei Fehler).
const newTag = ref('')
const savingTags = ref(false)

async function saveTags(tags: string[]) {
  if (!doc.value) return
  const previous = doc.value.tags
  doc.value.tags = tags
  savingTags.value = true
  try {
    const updated = await api.update(doc.value.id, { tags })
    doc.value.tags = updated.tags
    error.value = ''
  } catch (e) {
    doc.value.tags = previous
    error.value = (e as Error).message
  } finally {
    savingTags.value = false
  }
}

function removeTag(tag: string) {
  if (!doc.value) return
  saveTags(doc.value.tags.filter((t) => t !== tag))
}

function addTag() {
  const tag = newTag.value.trim()
  newTag.value = ''
  if (!doc.value || !tag || doc.value.tags.includes(tag)) return
  saveTags([...doc.value.tags, tag])
}

const ROLE_LABEL: Record<DocumentEntity['role'], string> = {
  sender: 'Absender',
  recipient: 'Empfänger',
  mentioned: 'Erwähnt',
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${kb.toFixed(0)} KB`
  return `${(kb / 1024).toFixed(1)} MB`
}

onMounted(async () => {
  try {
    const [config, detail] = await Promise.all([
      store.loadConfig(),
      api.get(props.id),
    ])
    doc.value = detail
    await loadDocsApi(config.onlyofficeUrl)
    // Identisch zur bisherigen Dokumentenansicht: Bearbeitungsmodus,
    // OnlyOffice speichert Änderungen über den Callback direkt in die
    // Ergebnis-.docx zurück (Autosave).
    const viewerConfig = await api.onlyofficeConfig(props.id, 'result')
    editor = createViewer('oo-editor', viewerConfig, 'desktop')
  } catch (e) {
    error.value = (e as Error).message
  }
})

onBeforeUnmount(() => {
  editor?.destroyEditor()
  editor = null
})
</script>

<template>
  <div class="editor-view">
    <div class="head">
      <button class="navbtn" @click="router.push({ name: 'documents' })">
        <MdiIcon :path="mdiArrowLeft" :size="16" /> Übersicht
      </button>
      <h2>{{ doc ? stem(doc.filename) : '…' }}</h2>
      <span class="hint">Änderungen werden automatisch gespeichert</span>
      <span class="spacer" />
      <a v-if="doc" :href="`/api/documents/${doc.id}/file/docx`">
        <button><MdiIcon :path="mdiDownload" :size="15" /> .docx</button>
      </a>
    </div>
    <p v-if="error" class="error">{{ error }}</p>

    <div ref="splitEl" class="split" :class="{ dragging }">
      <aside class="meta" :style="{ width: leftWidth + '%' }">
        <template v-if="doc">
          <dl class="fields">
            <dt>Dok.-Datum</dt>
            <dd>{{ fmtDate(doc.doc_date) }}</dd>
            <dt>Seiten</dt>
            <dd>{{ doc.page_count ?? '—' }}</dd>
            <dt>Größe</dt>
            <dd>{{ fmtSize(doc.size_bytes) }}</dd>
            <dt>Importiert am</dt>
            <dd>{{ fmtDateTime(doc.uploaded_at) }}</dd>
            <dt>Verarbeitet am</dt>
            <dd>{{ doc.processed_at ? fmtDateTime(doc.processed_at) : '—' }}</dd>
          </dl>

          <section v-if="doc.summary" class="block">
            <h3>Zusammenfassung</h3>
            <p class="summary">{{ doc.summary }}</p>
          </section>

          <section v-if="doc.entities.length" class="block">
            <h3>
              <MdiIcon :path="mdiAccountMultipleOutline" :size="15" /> Beteiligte
            </h3>
            <ul class="entities">
              <li v-for="(e, i) in doc.entities" :key="i" class="entity">
                <span class="role" :class="e.role">{{ ROLE_LABEL[e.role] }}</span>
                <div class="who">
                  <span v-if="e.name" class="name">{{ e.name }}</span>
                  <span v-if="e.company" class="company">{{ e.company }}</span>
                  <span v-if="e.address" class="addr">{{ e.address }}</span>
                  <a v-if="e.phone" :href="`tel:${e.phone}`">{{ e.phone }}</a>
                  <a v-if="e.email" :href="`mailto:${e.email}`">{{ e.email }}</a>
                </div>
              </li>
            </ul>
          </section>

          <section class="block">
            <h3><MdiIcon :path="mdiTagOutline" :size="15" /> Schlagworte</h3>
            <div class="tags">
              <span
                v-for="tag in doc.tags"
                :key="tag"
                class="tag"
                :class="{ unreadable: tag === 'Unlesbar' }"
              >
                {{ tag }}
                <button
                  class="tag-x"
                  title="Schlagwort entfernen"
                  :disabled="savingTags"
                  @click="removeTag(tag)"
                >
                  <MdiIcon :path="mdiClose" :size="13" />
                </button>
              </span>
              <span v-if="!doc.tags.length" class="none">keine</span>
            </div>
            <form class="add" @submit.prevent="addTag">
              <input
                v-model="newTag"
                type="text"
                placeholder="Schlagwort hinzufügen …"
                :disabled="savingTags"
              />
              <button
                type="submit"
                title="Hinzufügen"
                :disabled="savingTags || !newTag.trim()"
              >
                <MdiIcon :path="mdiPlus" :size="16" />
              </button>
            </form>
          </section>
        </template>
        <p v-else class="loading">Lade Metadaten …</p>
      </aside>

      <div
        class="splitter"
        title="Ziehen zum Verschieben"
        @pointerdown="onDragStart"
        @pointermove="onDragMove"
        @pointerup="onDragEnd"
      />

      <div class="editor-pane">
        <div id="oo-editor" class="oo-host"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.editor-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 0.6rem 1.2rem 0.8rem;
  gap: 0.5rem;
}
.head {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
  flex-shrink: 0;
}
.head h2 {
  margin: 0;
  font-size: 1.05rem;
}
.navbtn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
.hint {
  color: var(--text-dim);
  font-size: 0.82rem;
}
.spacer {
  flex: 1;
}
.head button {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}

/* Zweispaltiges Layout: Metadaten | Splitter | Editor */
.split {
  display: flex;
  flex: 1;
  min-height: 0;
}
/* Während des Ziehens: iframe schluckt keine Zeigerereignisse mehr */
.split.dragging .editor-pane {
  pointer-events: none;
}
.meta {
  min-width: 0;
  overflow-y: auto;
  padding-right: 0.4rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.fields {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.3rem 0.8rem;
  margin: 0;
  font-size: 0.88rem;
}
.fields dt {
  color: var(--text-dim);
}
.fields dd {
  margin: 0;
  font-variant-numeric: tabular-nums;
}
.block h3 {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin: 0 0 0.4rem;
  font-size: 0.9rem;
  color: var(--text-h);
}
.summary {
  margin: 0;
  font-size: 0.85rem;
  color: var(--text-dim);
  line-height: 1.4;
}
.entities {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.entity {
  display: flex;
  gap: 0.5rem;
  font-size: 0.85rem;
}
.role {
  flex-shrink: 0;
  align-self: flex-start;
  border-radius: 999px;
  padding: 0.05rem 0.5rem;
  font-size: 0.72rem;
  font-weight: 600;
  background: var(--bg-soft);
  color: var(--text-dim);
  border: 1px solid var(--border);
}
.role.sender {
  background: var(--accent-bg);
  color: var(--accent);
  border-color: transparent;
}
.role.recipient {
  background: rgba(22, 163, 74, 0.12);
  color: var(--ok);
  border-color: transparent;
}
.who {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 0.05rem;
}
.who .name {
  font-weight: 600;
  color: var(--text-h);
}
.who .company,
.who .addr {
  color: var(--text-dim);
}
.who a {
  color: var(--accent);
  text-decoration: none;
  word-break: break-all;
}
.who a:hover {
  text-decoration: underline;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}
.tag {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  background: var(--accent-bg);
  color: var(--accent);
  border-radius: 999px;
  padding: 0.05rem 0.2rem 0.05rem 0.55rem;
  font-size: 0.78rem;
  white-space: nowrap;
}
.tag.unreadable {
  background: rgba(220, 38, 38, 0.12);
  color: var(--err);
}
.tag-x {
  display: inline-flex;
  align-items: center;
  padding: 0;
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  opacity: 0.7;
}
.tag-x:hover:not(:disabled) {
  opacity: 1;
}
.none {
  color: var(--text-dim);
  font-size: 0.82rem;
}
/* Eingabezeile als eine zusammenhängende Pille: Feld + „+"-Knopf teilen
   sich einen Rahmen, der beim Fokus in Akzentfarbe aufleuchtet. */
.add {
  display: flex;
  align-items: stretch;
  margin-top: 0.6rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg);
  overflow: hidden;
  transition: border-color 0.15s;
}
.add:focus-within {
  border-color: var(--accent);
}
.add input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  padding: 0.35rem 0.9rem;
  font: inherit;
  font-size: 0.82rem;
  color: var(--text);
  outline: none;
}
.add button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 0;
  background: transparent;
  color: var(--accent);
  padding: 0 0.7rem;
}
.add button:hover:not(:disabled) {
  background: var(--accent);
  color: #fff;
}
.add button:disabled {
  color: var(--text-dim);
  cursor: default;
}
.loading {
  color: var(--text-dim);
  font-size: 0.85rem;
}

.splitter {
  flex: 0 0 8px;
  cursor: col-resize;
  position: relative;
}
.splitter::before {
  content: '';
  position: absolute;
  inset: 0 3px;
  background: var(--border);
  border-radius: 2px;
}
.splitter:hover::before,
.split.dragging .splitter::before {
  background: var(--accent);
}

.editor-pane {
  flex: 1;
  min-width: 0;
  display: flex;
}
.oo-host {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.error {
  color: var(--err);
}
</style>
