import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev config. In production (docker) the built static bundle is served by nginx
// (see Dockerfile + nginx.conf), which proxies /v1 to the api service.
// This dev proxy makes `npm run dev` talk to the api container directly.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/v1': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
