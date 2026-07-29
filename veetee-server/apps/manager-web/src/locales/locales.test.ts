import { describe, expect, it } from "vitest";

import { enUS } from "./en-US";
import { viVN } from "./vi-VN";

function keys(value: object, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return child && typeof child === "object" && !Array.isArray(child)
      ? keys(child, path)
      : [path];
  });
}

describe("manager locale catalogs", () => {
  it("keeps vi-VN and en-US keys in parity", () => {
    expect(keys(viVN).sort()).toEqual(keys(enUS).sort());
  });
});
