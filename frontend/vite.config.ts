import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-прокси: запросы /api/* уходят на бэкенд (FastAPI на :8000),
// префикс /api срезается. Так фронтенд использует относительные пути
// и не зависит от CORS/хоста.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
