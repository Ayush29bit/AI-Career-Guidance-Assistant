import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The API base URL is read at runtime from VITE_API_URL (see .env.example), so
// no dev proxy is configured here -- the backend already allows this origin in
// its CORS settings, and one less indirection makes a failed request easier to
// read in the network tab.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Fail loudly rather than silently moving to 5174, which the backend's CORS
    // origins do not include.
    strictPort: true,
  },
})
