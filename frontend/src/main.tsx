// Fraunces with its optical size, as the design's own font link has it (the ampersand and the letters of a large title
// change with the size); the plain weight file draws them all the same.
import '@fontsource-variable/fraunces/opsz.css'
import '@fontsource-variable/fraunces/opsz-italic.css'
import '@fontsource-variable/inter'
import '@fontsource-variable/oswald'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import App from './App'
import './i18n'
import './index.css'
import { followSystem } from './lib/theme'
import { bearTranslatedPages } from './lib/translatedPage'
import { AuthProvider } from './state/auth'

followSystem()

// The service worker from the start, for Web Push (`public/sw.js`).
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => void navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => undefined))
}

bearTranslatedPages()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
