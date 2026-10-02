import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
      coverage: {
        provider: 'v8',
        include: ['src/**/*.{ts,vue}'],
        exclude: [
          'src/main.ts',
          'src/router/index.ts',
          'src/types/**',
          'src/api/**',
          'src/env.d.ts',
        ],
        thresholds: {
          'src/composables/useSSE.ts': { statements: 80, lines: 80, functions: 80, branches: 80 },
          'src/stores/chat.ts': { statements: 80, lines: 80, functions: 80 },
        },
      },
    },
  }),
)
