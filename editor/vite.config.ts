import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (id.includes("monaco-editor") || id.includes("@monaco-editor/react")) {
            return "monaco";
          }
          if (id.includes("@xterm")) {
            return "terminal";
          }
          if (id.includes("react-pdf") || id.includes("pdfjs-dist")) {
            return "pdf";
          }
          if (id.includes("katex")) {
            return "katex";
          }
          if (id.includes("@xyflow/react") || id.includes("@dagrejs/dagre")) {
            return "graph";
          }
          if (
            id.includes("marked")
            || id.includes("highlight.js")
            || id.includes("dompurify")
          ) {
            return "content";
          }
          if (
            id.includes("/react/")
            || id.includes("react-dom")
            || id.includes("zustand")
          ) {
            return "vendor";
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.DAN_API_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
