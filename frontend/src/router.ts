import { createRouter, createWebHistory } from 'vue-router'
import DocumentsView from './views/DocumentsView.vue'
import EditorView from './views/EditorView.vue'
import LibraryView from './views/LibraryView.vue'
import LoginView from './views/LoginView.vue'
import ViewerView from './views/ViewerView.vue'
import { useAuthStore } from './stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView },
    // Startseite: Bibliothek der fertigen Dokumente (.docx)
    { path: '/', name: 'documents', component: LibraryView },
    // Upload + Warteschlange (frühere Startseite)
    { path: '/upload', name: 'upload', component: DocumentsView },
    // Vollbild-Editor: Ergebnis-.docx in OnlyOffice, speichert in place
    { path: '/edit/:id', name: 'editor', component: EditorView, props: true },
    // Side-by-Side-Ansicht (Original | OCR-Ergebnis)
    { path: '/doc/:id', name: 'viewer', component: ViewerView, props: true },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.check()
  if (to.name !== 'login' && !auth.loggedIn) {
    return { name: 'login', query: { weiter: to.fullPath } }
  }
  if (to.name === 'login' && auth.loggedIn) {
    return { name: 'documents' }
  }
})
