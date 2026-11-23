import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: `npm run dev` serves the UI on :5173 and forwards API and file calls to the FastAPI back end on :8765.
// Build: `npm run build` writes dist/, which FastAPI serves at / (committed, so staff need no Node).
const backend = 'http://127.0.0.1:8765'
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': backend, '/files': backend, '/audit': backend } },
  build: { outDir: 'dist', emptyOutDir: true },
})
