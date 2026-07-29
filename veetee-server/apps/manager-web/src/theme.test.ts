import { describe, expect, it } from "vitest";

import {
  applyThemeToRoot,
  normalizeThemePreference,
  persistThemePreference,
  readThemePreference,
  resolveTheme,
  THEME_STORAGE_KEY,
  writeThemePreference,
} from "./theme";

describe("Manager appearance preference", () => {
  it("falls back to the system preference for missing or invalid values", () => {
    expect(normalizeThemePreference(undefined)).toBe("system");
    expect(normalizeThemePreference("sepia")).toBe("system");
    expect(readThemePreference({ getItem: () => null, setItem: () => undefined })).toBe("system");
    expect(readThemePreference({ getItem: () => "sepia", setItem: () => undefined })).toBe("system");
  });

  it("lets explicit preferences override the operating system", () => {
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });

  it("persists a preference and tolerates blocked storage", () => {
    const entries = new Map<string, string>();
    const storage = {
      getItem: (key: string) => entries.get(key) ?? null,
      setItem: (key: string, value: string) => entries.set(key, value),
    };
    writeThemePreference(storage, "dark");
    expect(entries.get(THEME_STORAGE_KEY)).toBe("dark");
    expect(readThemePreference(storage)).toBe("dark");
    expect(() => writeThemePreference({ getItem: () => null, setItem: () => { throw new Error("blocked"); } }, "light")).not.toThrow();
    const blockedHost = Object.defineProperty({}, "localStorage", {
      get(): never { throw new Error("blocked"); },
    }) as { readonly localStorage: Storage };
    expect(() => persistThemePreference(blockedHost, "light")).not.toThrow();
    expect(readThemePreference({ getItem: () => { throw new Error("blocked"); }, setItem: () => undefined })).toBe("system");
  });

  it("applies resolved attributes and native control color scheme", () => {
    const root = { dataset: {} as DOMStringMap, style: { colorScheme: "" } };
    expect(applyThemeToRoot(root, "system", true)).toBe("dark");
    expect(root.dataset).toMatchObject({ themePreference: "system", theme: "dark" });
    expect(root.style.colorScheme).toBe("dark");
  });
});
