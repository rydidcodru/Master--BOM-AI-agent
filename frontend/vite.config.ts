import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// @types/node 없이 process.env를 쓰기 위한 최소 선언.
declare const process: { env: Record<string, string | undefined> };

// FastAPI 백엔드는 기본 http://127.0.0.1:8000.
// 개발 중 /api 프록시로 CORS 없이도 호출할 수 있게 한다(직접 URL 호출도 가능).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.DEV_PARTS_API_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
