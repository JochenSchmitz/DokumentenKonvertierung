<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'
import {
  mdiAccount,
  mdiCounter,
  mdiFileDocumentOutline,
  mdiLogout,
  mdiTagOutline,
  mdiTrayFull,
} from '@mdi/js'
import MdiIcon from './components/MdiIcon.vue'
import { useAuthStore } from './stores/auth'
import { useDocumentsStore } from './stores/documents'

const auth = useAuthStore()
const store = useDocumentsStore()
const router = useRouter()
const version = ref('')

// Splashscreen: beim App-Start 3 s das Logo zeigen, dann ausblenden
const splash = ref(true)
onMounted(() => {
  setTimeout(() => (splash.value = false), 3000)
})

async function logout() {
  await auth.logout()
  router.push({ name: 'login' })
}

function reload() {
  window.location.href = '/'
}

onMounted(async () => {
  try {
    version.value = (await (await fetch('/api/version')).json()).version
  } catch {
    /* Chip bleibt leer */
  }
})

// Footer-Status: alle 5 s aktualisieren, solange jemand angemeldet ist
let timer = 0 as ReturnType<typeof setInterval> | 0
watch(
  () => auth.loggedIn,
  (on) => {
    if (on && !timer) {
      store.fetchStatus()
      timer = setInterval(() => store.fetchStatus(), 5000)
    } else if (!on && timer) {
      clearInterval(timer)
      timer = 0
    }
  },
  { immediate: true },
)

function fmt(n: number): string {
  return n.toLocaleString('de-DE')
}
</script>

<template>
  <Transition name="splash">
    <div v-if="splash" class="splash">
      <img src="/logo.png" alt="FES Logo" />
      <span>FDS — FES Dokumentenservice</span>
    </div>
  </Transition>

  <div class="shell">
    <header class="topbar">
      <img src="/logo.png" alt="FES Logo" class="header-logo" />
      <button class="brand" title="Seite neu laden" @click="reload">
        FDS — FES Dokumentenservice
      </button>
      <nav v-if="auth.loggedIn" class="nav">
        <RouterLink :to="{ name: 'documents' }">Dokumente</RouterLink>
        <RouterLink :to="{ name: 'upload' }">Upload</RouterLink>
      </nav>
      <span class="spacer" />
      <button v-if="auth.loggedIn" class="logout" @click="logout">
        <MdiIcon :path="mdiLogout" /> Abmelden
      </button>
    </header>

    <main>
      <RouterView />
    </main>

    <footer class="footbar">
      <template v-if="auth.loggedIn">
        <span class="chip">
          <MdiIcon :path="mdiAccount" :size="15" /> {{ auth.email }}
        </span>
        <span v-if="store.status?.processing.length" class="chip busy">
          <span class="pulse" />
          <MdiIcon :path="mdiFileDocumentOutline" :size="15" />
          {{ store.status.processing.join(', ') }}
          <template v-if="store.status.currentPages">
            ({{ store.status.currentPages }} S.)
          </template>
        </span>
        <span v-if="store.status?.pending" class="chip">
          <MdiIcon :path="mdiTrayFull" :size="15" />
          Warteschlange: {{ store.status.pending }}
        </span>
        <span v-if="store.status" class="chip tokens">
          <MdiIcon :path="mdiCounter" :size="15" />
          {{ fmt(store.status.generatedTokens) }} erzeugt ·
          {{ fmt(store.status.promptTokens) }} verarbeitet ·
          {{ store.tokensPerSecond }}/s
        </span>
      </template>
      <span class="spacer" />
      <span v-if="version" class="chip version">
        <MdiIcon :path="mdiTagOutline" :size="15" /> v{{ version }}
      </span>
    </footer>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.topbar,
.footbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0 1rem;
  flex-shrink: 0;
}
.topbar {
  height: 3rem;
  border-bottom: 1px solid var(--border);
}
.footbar {
  height: 2.6rem;
  border-top: 1px solid var(--border);
  background: var(--bg-soft);
  overflow: hidden;
}
.brand {
  font-weight: 700;
  color: var(--text-h);
  font-size: 1.05rem;
  background: none;
  border: none;
  padding: 0.2rem 0;
}
.header-logo {
  height: 2.2rem;
  width: auto;
}
.nav {
  display: inline-flex;
  gap: 0.25rem;
  margin-left: 1rem;
}
.nav a {
  color: var(--text-dim);
  text-decoration: none;
  padding: 0.25rem 0.7rem;
  border-radius: 999px;
  font-size: 0.9rem;
}
.nav a:hover {
  color: var(--text);
  background: var(--bg-soft);
}
.nav a.router-link-active {
  color: var(--accent);
  background: var(--accent-bg);
  font-weight: 600;
}
.splash {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  background: var(--bg);
}
.splash img {
  width: min(340px, 60vw);
  height: auto;
  animation: splash-in 0.8s ease-out;
}
.splash span {
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--text-h);
  letter-spacing: 0.04em;
}
@keyframes splash-in {
  from { opacity: 0; transform: scale(0.92); }
  to { opacity: 1; transform: scale(1); }
}
.splash-leave-active {
  transition: opacity 0.4s ease;
}
.splash-leave-to {
  opacity: 0;
}
.spacer {
  flex: 1;
}
.logout {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
main {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg);
  padding: 0.15rem 0.7rem;
  font-size: 0.8rem;
  color: var(--text-dim);
  white-space: nowrap;
  max-width: 34rem;
  overflow: hidden;
  text-overflow: ellipsis;
}
.chip.busy {
  color: var(--text);
  border-color: var(--accent);
}
.chip.version,
.chip.tokens {
  font-variant-numeric: tabular-nums;
}
.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ok);
  animation: pulse 1.2s ease-in-out infinite;
  flex-shrink: 0;
}
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.35; transform: scale(0.75); }
}
</style>
