import fs from 'node:fs'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// Same mkcert certificate as the server (step 11). Served over https so the page and the
// API / socket (https:// + wss://) are all encrypted.
const certDir = new URL('../server/certs/', import.meta.url)

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    https: {
      cert: fs.readFileSync(new URL('server.pem', certDir)),
      key: fs.readFileSync(new URL('server-key.pem', certDir)),
    },
  },
})
