<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  mdiClose,
  mdiDelete,
  mdiDownload,
  mdiMagnify,
  mdiReload,
  mdiTagOutline,
} from '@mdi/js'
import MdiIcon from '../components/MdiIcon.vue'
import UploadZone from '../components/UploadZone.vue'
import { useDocumentsStore } from '../stores/documents'
import type { DocumentOut } from '../api'

const store = useDocumentsStore()

onMounted(async () => {
  await store.fetch()
  if (store.busyCount > 0) store.ensurePolling()
})

// Live-Suche: ab dem 4. Zeichen sofort (debounced) suchen,
// bei leerem Feld wieder alle Dokumente zeigen.
let debounce = 0 as ReturnType<typeof setTimeout> | 0
watch(
  () => store.query,
  (q) => {
    if (debounce) clearTimeout(debounce)
    if (q.trim().length >= 4 || q.trim().length === 0) {
      debounce = setTimeout(() => store.search(), 250)
    }
  },
)
onUnmounted(() => {
  if (debounce) clearTimeout(debounce)
})

// Warteschlange: immer ungefiltert — die Suche wirkt nur rechts
const queueDocs = computed(() =>
  store.docs.filter((d) => d.status !== 'done'),
)
const doneDocs = computed(() =>
  (store.results ?? store.docs).filter((d) => d.status === 'done'),
)

const statusLabel: Record<DocumentOut['status'], string> = {
  pending: '⏳ wartet',
  processing: '⚙️ liest …',
  done: '✅ fertig',
  error: '❌ Fehler',
}

function fmtSize(bytes: number): string {
  return bytes > 1048576
    ? `${(bytes / 1048576).toFixed(1)} MB`
    : `${Math.round(bytes / 1024)} kB`
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString('de-DE') : '—'
}

async function remove(doc: DocumentOut) {
  if (confirm(`„${doc.filename}" wirklich löschen?`)) await store.remove(doc.id)
}

// ── Splitter: linke Spaltenbreite per Drag, gemerkt im localStorage ──
const leftPct = ref(Number(localStorage.getItem('splitter') ?? 38))
const tiles = ref<HTMLElement>()
let dragging = false

function startDrag() {
  dragging = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
function onMove(e: PointerEvent) {
  if (!dragging || !tiles.value) return
  const rect = tiles.value.getBoundingClientRect()
  const pct = ((e.clientX - rect.left) / rect.width) * 100
  leftPct.value = Math.min(75, Math.max(20, pct))
}
function stopDrag() {
  if (!dragging) return
  dragging = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  localStorage.setItem('splitter', String(Math.round(leftPct.value)))
}
onMounted(() => {
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', stopDrag)
})
onUnmounted(() => {
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', stopDrag)
})
</script>

<template>
  <div ref="tiles" class="tiles">
    <div class="col-left" :style="{ width: leftPct + '%' }">
      <section class="tile">
        <UploadZone />
      </section>

      <section class="tile grow">
        <header>Warteschlange ({{ queueDocs.length }})</header>
        <div class="tile-body">
          <p v-if="!queueDocs.length" class="empty">Nichts in Arbeit.</p>
          <ul v-else class="queue">
            <li v-for="doc in queueDocs" :key="doc.id">
              <span class="status">{{ statusLabel[doc.status] }}</span>
              <span class="name" :title="doc.filename">{{ doc.filename }}</span>
              <span class="q-actions">
                <button
                  v-if="doc.status === 'error'"
                  title="Neu verarbeiten"
                  @click="store.reprocess(doc.id)"
                ><MdiIcon :path="mdiReload" :size="15" /></button>
                <button title="Löschen" @click="remove(doc)">
                  <MdiIcon :path="mdiDelete" :size="15" />
                </button>
              </span>
              <div v-if="doc.error" class="error">{{ doc.error }}</div>
            </li>
          </ul>
        </div>
      </section>
    </div>

    <div
      class="splitter"
      title="Ziehen zum Anpassen der Breite"
      @pointerdown.prevent="startDrag"
    />

    <section class="tile col-right">
      <header class="right-head">
        <span>Bearbeitete Dokumente ({{ doneDocs.length }})</span>
        <span class="search">
          <MdiIcon :path="mdiMagnify" :size="16" />
          <input
            v-model="store.query"
            type="search"
            placeholder="Live-Suche ab 4 Zeichen — Name, Schlagworte, Volltext …"
          />
          <span v-if="store.query && store.query.trim().length < 4" class="hint">
            noch {{ 4 - store.query.trim().length }} Zeichen …
          </span>
        </span>
        <span v-if="store.selectedTags.length" class="tag-filter">
          <button
            v-for="tag in store.selectedTags"
            :key="tag"
            class="tag active"
            title="Filter entfernen"
            @click="store.toggleTag(tag)"
          >
            <MdiIcon :path="mdiTagOutline" :size="13" />
            {{ tag }}
            <MdiIcon :path="mdiClose" :size="13" />
          </button>
        </span>
      </header>
      <div class="tile-body">
        <p v-if="store.error" class="error">{{ store.error }}</p>
        <table v-if="doneDocs.length">
          <thead>
            <tr>
              <th>Dokument</th>
              <th>Seiten</th>
              <th>Dok.-Datum</th>
              <th>Schlagworte</th>
              <th>Größe</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="doc in doneDocs" :key="doc.id">
              <td>
                <RouterLink
                  :to="{ name: 'viewer', params: { id: doc.id } }"
                  class="doc-link"
                >{{ doc.filename }}</RouterLink>
                <div v-if="doc.summary" class="summary">{{ doc.summary }}</div>
              </td>
              <td>{{ doc.page_count ?? '—' }}</td>
              <td>{{ fmtDate(doc.doc_date) }}</td>
              <td>
                <button
                  v-for="tag in doc.tags"
                  :key="tag"
                  class="tag"
                  :class="{ active: store.selectedTags.includes(tag) }"
                  :title="store.selectedTags.includes(tag)
                    ? 'Filter entfernen'
                    : 'Nach diesem Schlagwort filtern'"
                  @click="store.toggleTag(tag)"
                >{{ tag }}</button>
              </td>
              <td>{{ fmtSize(doc.size_bytes) }}</td>
              <td class="actions">
                <a
                  :href="`/api/documents/${doc.id}/file/docx`"
                  title="Word-Datei herunterladen"
                ><button><MdiIcon :path="mdiDownload" :size="15" /> .docx</button></a>
                <button title="Neu verarbeiten" @click="store.reprocess(doc.id)">
                  <MdiIcon :path="mdiReload" :size="15" />
                </button>
                <button title="Löschen" @click="remove(doc)">
                  <MdiIcon :path="mdiDelete" :size="15" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty">
          Noch keine fertigen Dokumente — links hochladen.
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.tiles {
  display: flex;
  height: 100%;
  padding: 0.8rem;
  gap: 0;
}
.col-left {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
  min-width: 0;
}
.col-right {
  flex: 1;
  min-width: 0;
}
.splitter {
  width: 9px;
  margin: 0 2px;
  cursor: col-resize;
  border-radius: 4px;
  background: transparent;
  flex-shrink: 0;
  position: relative;
}
.splitter::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 4px;
  width: 1px;
  background: var(--border);
}
.splitter:hover {
  background: var(--accent-bg);
}
.tile {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  min-height: 0;
}
.tile.grow {
  flex: 1;
}
.tile > header {
  padding: 0.45rem 0.9rem;
  background: var(--bg-soft);
  border-bottom: 1px solid var(--border);
  font-weight: 600;
  font-size: 0.88rem;
  flex-shrink: 0;
}
.tile-body {
  overflow: auto;
  min-height: 0;
  flex: 1;
  padding: 0.4rem 0.6rem;
}
.right-head {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  flex-wrap: wrap;
}
.search {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-weight: 400;
}
.search input {
  min-width: 22rem;
  padding: 0.25rem 0.6rem;
}
.hint {
  color: var(--text-dim);
  font-size: 0.8rem;
}
.queue {
  list-style: none;
  margin: 0;
  padding: 0;
}
.queue li {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0.35rem 0.3rem;
  border-bottom: 1px solid var(--border);
  font-size: 0.88rem;
}
.queue .status {
  flex-shrink: 0;
  width: 5.5rem;
}
.queue .name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.q-actions {
  display: inline-flex;
  gap: 0.25rem;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th {
  text-align: left;
  color: var(--text-dim);
  font-weight: 600;
  font-size: 0.85rem;
  border-bottom: 2px solid var(--border);
  padding: 0.4rem 0.6rem;
}
td {
  border-bottom: 1px solid var(--border);
  padding: 0.5rem 0.6rem;
  vertical-align: top;
}
.doc-link {
  color: var(--accent);
  font-weight: 600;
  text-decoration: none;
}
.doc-link:hover {
  text-decoration: underline;
}
.summary {
  color: var(--text-dim);
  font-size: 0.85rem;
  max-width: 46ch;
}
.tag {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  background: var(--accent-bg);
  color: var(--accent);
  border: 1px solid transparent;
  border-radius: 999px;
  padding: 0.05rem 0.55rem;
  font-size: 0.78rem;
  margin: 0 0.25rem 0.25rem 0;
  white-space: nowrap;
  cursor: pointer;
}
.tag:hover {
  border-color: var(--accent);
}
.tag.active {
  background: var(--accent);
  color: #fff;
}
.tag-filter {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.15rem;
  font-weight: 400;
}
.actions {
  white-space: nowrap;
}
.actions > * {
  margin-left: 0.25rem;
}
.actions button {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}
.error {
  color: var(--err);
  font-size: 0.85rem;
  width: 100%;
}
.empty {
  color: var(--text-dim);
  text-align: center;
  padding: 1.5rem;
}
</style>
