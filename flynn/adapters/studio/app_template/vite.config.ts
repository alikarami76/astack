import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { flynnAi } from "./flynn-ai";

export default defineConfig({
  plugins: [react(), flynnAi()],
  server: { host: "127.0.0.1" },
});
