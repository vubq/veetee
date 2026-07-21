import "@fontsource/be-vietnam-pro/vietnamese-400.css";
import "@fontsource/be-vietnam-pro/vietnamese-500.css";
import "@fontsource/be-vietnam-pro/vietnamese-600.css";
import "@fontsource/be-vietnam-pro/vietnamese-700.css";
import "@fontsource/space-grotesk/vietnamese-500.css";
import "@fontsource/space-grotesk/vietnamese-600.css";
import "@fontsource/space-grotesk/vietnamese-700.css";
import { VueQueryPlugin } from "@tanstack/vue-query";
import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import { i18n } from "./i18n";
import "../../../prototypes/manager-web/styles.css";
import "./app.css";

createApp(App).use(createPinia()).use(VueQueryPlugin).use(i18n).mount("#app");
