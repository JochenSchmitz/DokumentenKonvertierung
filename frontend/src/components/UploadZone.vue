<script setup lang="ts">
import { ref } from 'vue'
import { useDocumentsStore } from '../stores/documents'

const store = useDocumentsStore()
const dragging = ref(false)
const fileInput = ref<HTMLInputElement>()

function onFiles(list: FileList | null) {
  if (!list) return
  store.upload(Array.from(list))
  if (fileInput.value) fileInput.value.value = ''
}

function onDrop(e: DragEvent) {
  dragging.value = false
  onFiles(e.dataTransfer?.files ?? null)
}
</script>

<template>
  <div
    class="dropzone"
    :class="{ dragging, uploading: store.uploading }"
    @dragover.prevent="dragging = true"
    @dragleave="dragging = false"
    @drop.prevent="onDrop"
    @click="fileInput?.click()"
  >
    <input
      ref="fileInput"
      type="file"
      multiple
      accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.msg,.doc,.docx,.zip"
      hidden
      @change="onFiles(($event.target as HTMLInputElement).files)"
    />
    <template v-if="store.uploading">⏳ Lade hoch …</template>
    <template v-else>
      📤 Dokumente hierher ziehen oder klicken (PDF, Bilder, Word,
      Outlook-.msg, ZIP — mehrere möglich)
    </template>
  </div>
</template>

<style scoped>
.dropzone {
  border: 2px dashed var(--border);
  border-radius: 10px;
  padding: 1.6rem;
  text-align: center;
  color: var(--text-dim);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.dropzone:hover,
.dropzone.dragging {
  border-color: var(--accent);
  background: var(--accent-bg);
  color: var(--text);
}
.dropzone.uploading {
  pointer-events: none;
  opacity: 0.7;
}
</style>
