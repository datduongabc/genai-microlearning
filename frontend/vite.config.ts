/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    globals: true,
  },
  server: {
    // The source is bind-mounted from Windows, and a change made there never
    // reaches the container as an inotify event. Without polling the dev server
    // keeps serving whatever it transformed first, so edits (and the
    // production-looking page a teacher would test against) go stale silently.
    watch: { usePolling: true, interval: 300 },
    proxy: {
      // BACKEND_URL is set to the docker-compose service name when running
      // in a container (localhost inside the frontend container would not
      // reach the separate backend container); defaults to localhost for
      // running `npm run dev` directly on the host.
      '/api': {
        target: process.env.BACKEND_URL || 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
