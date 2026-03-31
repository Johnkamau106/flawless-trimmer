import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  cacheDir: '.vite',
  server: {
    middlewareMode: false,
    warmup: {
      clientFiles: ['./src/**/*.jsx', './src/main.jsx'],
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5000',
        changeOrigin: true,
      },
      '/api/thumbnail': {
        target: 'http://127.0.0.1:5000',
        changeOrigin: true,
      },
    },
  },
  optimizeDeps: {
    include: ['react', 'react-dom', 'react-player', 'hls.js', 'axios'],
    esbuildOptions: {
      target: 'esnext',
    },
  },
})
