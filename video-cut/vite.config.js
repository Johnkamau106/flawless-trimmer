import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Avoid Windows file locking under /mnt/c by using WSL tmp for cache
  cacheDir: '/tmp/vite-cache',
})
