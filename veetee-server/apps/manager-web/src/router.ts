import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";

import ManagerRouteOutlet from "./components/ManagerRouteOutlet.vue";
import type { ManagerPage } from "./types/manager";
import type { VtIconName } from "./components/ui/VtIcon.vue";

export interface ManagerRouteMeta {
  page: ManagerPage;
  titleKey: string;
  labelKey: string;
  shortKey: string;
  icon: VtIconName;
  density: "airy" | "comfortable" | "compact";
}

const routeMeta: ManagerRouteMeta[] = [
  { page: "overview", titleKey: "routes.overview.title", labelKey: "nav.overview.label", shortKey: "nav.overview.short", icon: "overview", density: "airy" },
  { page: "devices", titleKey: "routes.devices.title", labelKey: "nav.devices.label", shortKey: "nav.devices.short", icon: "device", density: "compact" },
  { page: "agents", titleKey: "routes.agents.title", labelKey: "nav.agents.label", shortKey: "nav.agents.short", icon: "agent", density: "comfortable" },
  { page: "providers", titleKey: "routes.providers.title", labelKey: "nav.providers.label", shortKey: "nav.providers.short", icon: "provider", density: "comfortable" },
  { page: "lab", titleKey: "routes.lab.title", labelKey: "nav.lab.label", shortKey: "nav.lab.short", icon: "lab", density: "comfortable" },
  { page: "resources", titleKey: "routes.resources.title", labelKey: "nav.resources.label", shortKey: "nav.resources.short", icon: "resource", density: "compact" },
  { page: "operations", titleKey: "routes.operations.title", labelKey: "nav.operations.label", shortKey: "nav.operations.short", icon: "telemetry", density: "compact" },
];

export const managerRoutes = routeMeta;

const routes: RouteRecordRaw[] = routeMeta.map((meta): RouteRecordRaw => ({
  path: `/${meta.page}`,
  name: meta.page,
  component: ManagerRouteOutlet,
  meta: { ...meta },
}));
routes.push({ path: "/:pathMatch(.*)*", redirect: "/overview" });

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior(_to, _from, savedPosition) {
    if (savedPosition) return savedPosition;
    return { top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" };
  },
});
