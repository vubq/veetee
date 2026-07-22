import assert from "node:assert/strict";
import { cp, mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import { buildUiPack, inspectUiPackBuffer } from "./ui-pack.mjs";

const signal = resolve("ui-packs/signal");

test("builds and inspects a deterministic Signal UI Pack", async () => {
  const first = await buildUiPack(signal);
  const second = await buildUiPack(signal);
  assert.deepEqual(first.buffer, second.buffer);
  const inspected = await inspectUiPackBuffer(first.buffer);
  assert.equal(inspected.manifest.theme_id, "signal");
  assert.equal(inspected.theme.composition, "signal");
  assert.ok(inspected.entries.some((entry) => entry.name === "strings/vi-VN.json"));
});

for (const theme of ["monolith", "quiet"]) {
  test(`builds the ${theme} standard UI Pack`, async () => {
    const built = await buildUiPack(resolve(`ui-packs/${theme}`));
    const inspected = await inspectUiPackBuffer(built.buffer);
    assert.equal(inspected.manifest.theme_id, theme);
    assert.equal(inspected.theme.composition, theme);
  });
}

test("rejects member corruption", async () => {
  const built = await buildUiPack(signal);
  const corrupted = Buffer.from(built.buffer);
  corrupted[corrupted.length - 1] ^= 0xff;
  await assert.rejects(inspectUiPackBuffer(corrupted), /SHA-256 mismatch/);
});

test("rejects executable members and path traversal", async () => {
  const root = await mkdtemp(join(tmpdir(), "veetee-ui-pack-"));
  await writeFile(join(root, "manifest.json"), "{}");
  await writeFile(join(root, "theme.json"), "{}");
  await mkdir(join(root, "strings"));
  await writeFile(join(root, "strings", "vi-VN.json"), "{}");
  await writeFile(join(root, "runtime.wasm"), "unsafe");
  await assert.rejects(buildUiPack(root), /Unsafe|not allowed/);
});

test("requires every UI ABI state in locale strings", async () => {
  const root = await mkdtemp(join(tmpdir(), "veetee-ui-pack-states-"));
  await cp(signal, root, { recursive: true });
  const stringsPath = join(root, "strings", "vi-VN.json");
  const strings = JSON.parse(await readFile(stringsPath, "utf8"));
  delete strings.states.closing;
  await writeFile(stringsPath, JSON.stringify(strings));

  await assert.rejects(buildUiPack(root), /strings\/vi-VN\.states\.closing/);
});
