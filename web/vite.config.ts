import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    // O front chama /api no mesmo origin e o Vite faz proxy para o adaptador; assim o
    // cookie httpOnly viaja sem CORS e sem SameSite=None.
    proxy: { "/api": { target: "http://localhost:8001", changeOrigin: true } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
  },
});
