import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const apiProxy =
  (typeof process !== "undefined" && process.env && process.env.UGRILE_API_PROXY) ||
  "http://127.0.0.1:8080";

const apiProxyRoutes = {
  "/api": {
    target: apiProxy,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: apiProxyRoutes,
  },
  preview: {
    host: "127.0.0.1",
    port: 5174,
    proxy: apiProxyRoutes,
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
});
