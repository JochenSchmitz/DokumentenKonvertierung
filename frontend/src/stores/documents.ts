import { defineStore } from 'pinia'
import {
  api,
  type AppConfig,
  type DocumentOut,
  type ProcessingStatus,
  type UploadSkipped,
} from '../api'

export const useDocumentsStore = defineStore('documents', {
  state: () => ({
    docs: [] as DocumentOut[],
    // Suchtreffer für die Kachel "Bearbeitete Dokumente" — null = kein
    // aktiver Filter. Die Warteschlange nutzt immer die ungefilterte Liste.
    results: null as DocumentOut[] | null,
    query: '',
    selectedTags: [] as string[],
    loading: false,
    uploading: false,
    // Beim letzten Upload abgelehnte Dateien (Duplikate, falscher Typ)
    skippedUploads: [] as UploadSkipped[],
    error: '' as string,
    config: null as AppConfig | null,
    pollTimer: 0 as ReturnType<typeof setInterval> | 0,
    status: null as ProcessingStatus | null,
    tokensPerSecond: 0,
    lastTokenSample: null as { tokens: number; at: number } | null,
  }),

  getters: {
    busyCount: (s) =>
      s.docs.filter((d) => d.status === 'pending' || d.status === 'processing')
        .length,
  },

  actions: {
    async loadConfig() {
      if (!this.config) this.config = await api.config()
      return this.config
    },

    async fetch() {
      this.loading = true
      try {
        this.docs = await api.list()
        this.error = ''
      } catch (e) {
        this.error = (e as Error).message
      } finally {
        this.loading = false
      }
    },

    /** Suche (ab 4 Zeichen) und/oder Tag-Filter — nur für die Ergebnisliste. */
    async search() {
      const q = this.query.trim()
      const effectiveQ = q.length >= 4 ? q : ''
      if (!effectiveQ && this.selectedTags.length === 0) {
        this.results = null
        return
      }
      try {
        this.results = await api.list(effectiveQ, this.selectedTags)
        this.error = ''
      } catch (e) {
        this.error = (e as Error).message
      }
    },

    /** Schlagwort an-/abwählen (Klick auf einen Tag). */
    async toggleTag(tag: string) {
      if (this.selectedTags.includes(tag)) {
        this.selectedTags = this.selectedTags.filter((t) => t !== tag)
      } else {
        this.selectedTags = [...this.selectedTags, tag]
      }
      await this.search()
    },

    async upload(files: File[]) {
      if (!files.length) return
      this.uploading = true
      try {
        const result = await api.upload(files)
        this.skippedUploads = result.skipped
        this.error = ''
        await this.fetch()
        this.ensurePolling()
      } catch (e) {
        this.error = (e as Error).message
      } finally {
        this.uploading = false
      }
    },

    async remove(id: string) {
      await api.remove(id)
      await this.fetch()
    },

    async reprocess(id: string) {
      await api.reprocess(id)
      await this.fetch()
      this.ensurePolling()
    },

    /** Lebenszeichen: was wird verarbeitet, wie viele Tokens fließen? */
    async fetchStatus() {
      try {
        const s = await api.status()
        if (this.lastTokenSample && s.generatedTokens >= this.lastTokenSample.tokens) {
          const seconds = (Date.now() - this.lastTokenSample.at) / 1000
          if (seconds > 0) {
            this.tokensPerSecond = Math.round(
              (s.generatedTokens - this.lastTokenSample.tokens) / seconds,
            )
          }
        }
        this.lastTokenSample = { tokens: s.generatedTokens, at: Date.now() }
        this.status = s
      } catch {
        /* Statusanzeige ist unkritisch */
      }
    },

    /** Solange Dokumente in Arbeit sind, Liste + Status alle 5 s aktualisieren. */
    ensurePolling() {
      if (this.pollTimer) return
      this.pollTimer = setInterval(async () => {
        await Promise.all([this.fetch(), this.fetchStatus(), this.search()])
        if (this.busyCount === 0 && this.pollTimer) {
          clearInterval(this.pollTimer)
          this.pollTimer = 0
        }
      }, 5000)
    },
  },
})
