<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { mdiArrowLeft, mdiDownload } from '@mdi/js'
import MdiIcon from '../components/MdiIcon.vue'
import { api, type DocumentDetail } from '../api'
import { useDocumentsStore } from '../stores/documents'
import { createViewer, loadDocsApi } from '../onlyoffice'

const props = defineProps<{ id: string }>()
const store = useDocumentsStore()

const doc = ref<DocumentDetail | null>(null)
const error = ref('')
let editor: { destroyEditor: () => void } | null = null

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
      <RouterLink :to="{ name: 'documents' }" class="back">
        <MdiIcon :path="mdiArrowLeft" :size="16" /> Übersicht
      </RouterLink>
      <h2>{{ doc?.filename ?? '…' }}</h2>
      <span class="hint">Änderungen werden automatisch gespeichert</span>
      <span class="spacer" />
      <a v-if="doc" :href="`/api/documents/${doc.id}/file/docx`">
        <button><MdiIcon :path="mdiDownload" :size="15" /> .docx</button>
      </a>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <div id="oo-editor" class="oo-host"></div>
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
.back {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  color: var(--accent);
  text-decoration: none;
  font-size: 0.9rem;
}
.back:hover {
  text-decoration: underline;
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
.oo-host {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.error {
  color: var(--err);
}
</style>
