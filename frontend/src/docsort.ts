/** Gemeinsame Spalten-Sortierung für die Dokumenttabellen
 * (Bibliothek und Upload-Seite). */

import { computed, ref } from 'vue'
import type { DocumentOut } from './api'

export type SortKey =
  | 'filename'
  | 'page_count'
  | 'doc_date'
  | 'tags'
  | 'uploaded_at'
  | 'processed_at'
  | 'size_bytes'

/** Dateiname ohne Endung — angezeigt wird ohnehin nur die .docx. */
export function stem(name: string): string {
  return name.replace(/\.[^.]+$/, '')
}

export function useDocSort(docs: () => DocumentOut[]) {
  // Standard: zuletzt fertig verarbeitete Dokumente oben
  const sortKey = ref<SortKey>('processed_at')
  const sortDir = ref<1 | -1>(-1)

  function setSort(key: SortKey) {
    if (sortKey.value === key) {
      sortDir.value = sortDir.value === 1 ? -1 : 1
    } else {
      sortKey.value = key
      // Texte initial aufsteigend, Zahlen/Daten absteigend (Neuestes zuerst)
      sortDir.value = key === 'filename' || key === 'tags' ? 1 : -1
    }
  }

  function compare(a: DocumentOut, b: DocumentOut): number {
    switch (sortKey.value) {
      case 'filename':
        return stem(a.filename).localeCompare(stem(b.filename), 'de', {
          sensitivity: 'base',
        })
      case 'page_count':
        return (a.page_count ?? -1) - (b.page_count ?? -1)
      case 'doc_date':
        return (a.doc_date ?? '').localeCompare(b.doc_date ?? '')
      case 'tags':
        return a.tags.join(', ').localeCompare(b.tags.join(', '), 'de', {
          sensitivity: 'base',
        })
      case 'uploaded_at':
        return a.uploaded_at.localeCompare(b.uploaded_at)
      case 'processed_at':
        return (a.processed_at ?? '').localeCompare(b.processed_at ?? '')
      case 'size_bytes':
        return a.size_bytes - b.size_bytes
    }
  }

  const sorted = computed(() =>
    docs()
      .slice()
      .sort((a, b) => sortDir.value * compare(a, b)),
  )

  return { sortKey, sortDir, setSort, sorted }
}

export function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString('de-DE') : '—'
}

export function fmtDateTime(iso: string | null): string {
  return iso
    ? new Date(iso).toLocaleString('de-DE', {
        dateStyle: 'short',
        timeStyle: 'short',
      })
    : '—'
}
