import { defineConfig } from 'vite'
import solid from 'vite-plugin-solid'

export default defineConfig({
  plugins: [solid()],
  build: {
    target: 'es2022',
    sourcemap: true,
    minify: 'esbuild',
    rollupOptions: {
      output: {
        manualChunks: {
          solid: ['solid-js'],
          ui: ['@solidjs/meta']
        }
      }
    },
    outDir: '../assistant/backend/static',
    emptyOutDir: true
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/chat': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/users': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/memory': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/brain': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/files': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/settings': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/tasks': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/db': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/feedback': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/correction': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/conversations': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/assistant': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/search': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/og-preview': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/transcribe': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
      '/summarize': {
        target: 'http://assistant:8000',
        changeOrigin: true,
      },
    },
  },
})