import { defineConfig } from 'vitest/config'
import solid from 'vite-plugin-solid'

export default defineConfig({
  plugins: [solid({ dev: true })],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./test-setup.ts'],
    globals: true,
    transformMode: {
      web: ['**/*.tsx'],
    },
  },
  esbuild: {
    jsx: 'preserve',
    jsxFactory: 'h',
    jsxFragment: 'Fragment',
  },
})