import { defineConfig } from 'vite'
import solid from 'vite-plugin-solid'

export default defineConfig({
  plugins: [solid()],
  // For development, don't set a base path for clean routing
  // In production, we can add it back in the deployment pipeline if needed
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
    host: true
  }
})