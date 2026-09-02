import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: true,
    // The theme provider writes to document.head and documentElement, so the
    // tests need a real DOM rather than assertions about strings.
    environment: 'jsdom',
    setupFiles: ['tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
