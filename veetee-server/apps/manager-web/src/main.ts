import { createApp } from "vue";

import prototypeScript from "../../../prototypes/manager-web/app.js?raw";
import prototypePage from "../../../prototypes/manager-web/index.html?raw";
import "../../../prototypes/manager-web/styles.css";

const body = prototypePage.match(/<body>([\s\S]*?)<script src="app\.js"><\/script>[\s\S]*?<\/body>/)?.[1];
if (!body) throw new Error("Unable to load the approved Manager Web prototype");

const apiBaseUrl = import.meta.env.VITE_MANAGER_API_URL ?? "http://127.0.0.1:8001";

createApp({
  template: body,
  async mounted(): Promise<void> {
    // The reviewed prototype remains the visual source of truth while its data
    // layer is moved behind Vue and Manager API incrementally.
    Function(prototypeScript)();
    const health = document.querySelector<HTMLElement>(".mini-health");
    try {
      const response = await fetch(`${apiBaseUrl}/health/live`);
      if (!response.ok) throw new Error(String(response.status));
      if (health) health.innerHTML = "<i></i> Manager API và voice stack sẵn sàng";
    } catch {
      if (health) {
        health.innerHTML = "<i></i> Manager API chưa kết nối";
        health.classList.add("degraded");
      }
    }
  },
}).mount("#app");
