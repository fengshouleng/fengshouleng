import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/** 后端地址：本地默认 uvicorn 8000，可用环境变量覆盖（不暴露给浏览器）。 */
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // 前端统一以 /api 前缀发请求，dev 阶段由 Vite 反代到后端；
      // 生产由 Nginx 承担同一职责（见 docs/10-arch.md ADR-008）。
      '/api': {
        target: API_PROXY_TARGET,
        changeOrigin: true,
        secure: false,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    css: false,
    restoreMocks: true,
  },
});
