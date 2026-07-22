import vue from "@vitejs/plugin-vue";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, ".", "");
  const allowedHosts = (environment.VEETEE_WEB_ALLOWED_HOSTS ?? "")
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);
  return {
    plugins: [vue()],
    server: {
      allowedHosts,
      fs: { allow: ["../.."] },
    },
  };
});
