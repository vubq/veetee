import { computed, readonly, ref } from "vue";

export const THEME_STORAGE_KEY = "veetee.manager.theme";

export type ThemePreference = "light" | "system" | "dark";
export type ResolvedTheme = Exclude<ThemePreference, "system">;

type ThemeStorage = Pick<Storage, "getItem" | "setItem">;
type ThemeStorageHost = { readonly localStorage: ThemeStorage };
type ThemeRoot = Pick<HTMLElement, "dataset"> & { style: Pick<CSSStyleDeclaration, "colorScheme"> };

export function normalizeThemePreference(value: unknown): ThemePreference {
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

export function resolveTheme(
  preference: ThemePreference,
  systemPrefersDark: boolean,
): ResolvedTheme {
  return preference === "system" ? (systemPrefersDark ? "dark" : "light") : preference;
}

export function readThemePreference(storage?: ThemeStorage | null): ThemePreference {
  if (!storage) return "system";
  try {
    return normalizeThemePreference(storage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "system";
  }
}

export function writeThemePreference(
  storage: ThemeStorage | null | undefined,
  preference: ThemePreference,
): void {
  try {
    storage?.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // A blocked storage backend must not make the appearance control unusable.
  }
}

export function persistThemePreference(
  host: ThemeStorageHost | null | undefined,
  nextPreference: ThemePreference,
): void {
  try {
    writeThemePreference(host?.localStorage, nextPreference);
  } catch {
    // Accessing localStorage itself can throw in a blocked or sandboxed context.
  }
}

export function applyThemeToRoot(
  root: ThemeRoot,
  preference: ThemePreference,
  systemPrefersDark: boolean,
): ResolvedTheme {
  const resolved = resolveTheme(preference, systemPrefersDark);
  root.dataset.themePreference = preference;
  root.dataset.theme = resolved;
  root.style.colorScheme = resolved;
  return resolved;
}

const mediaQuery = typeof window === "undefined"
  ? undefined
  : window.matchMedia("(prefers-color-scheme: dark)");
const initialPreference = typeof document === "undefined"
  ? "system"
  : normalizeThemePreference(document.documentElement.dataset.themePreference);
const preference = ref<ThemePreference>(initialPreference);
const systemPrefersDark = ref(mediaQuery?.matches ?? false);
const resolvedTheme = computed(() => resolveTheme(preference.value, systemPrefersDark.value));

function applyTheme(): void {
  if (typeof document === "undefined") return;
  applyThemeToRoot(document.documentElement, preference.value, systemPrefersDark.value);
}

function setPreference(nextPreference: ThemePreference): void {
  preference.value = normalizeThemePreference(nextPreference);
  applyTheme();
  persistThemePreference(typeof window === "undefined" ? undefined : window, preference.value);
}

mediaQuery?.addEventListener("change", (event) => {
  systemPrefersDark.value = event.matches;
  if (preference.value === "system") applyTheme();
});

applyTheme();

export function useTheme() {
  return {
    preference: readonly(preference),
    resolvedTheme,
    setPreference,
  };
}
