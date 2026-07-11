import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build output goes straight into the Python package so it ships in the wheel.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/insyte/studio_dist",
    emptyOutDir: true,
  },
  server: {
    // `npm run dev` proxies the API to a running `insyte studio` backend.
    proxy: {
      "/api": "http://127.0.0.1:3838",
    },
  },
});
