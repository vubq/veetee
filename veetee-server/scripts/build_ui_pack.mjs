import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import { buildUiPack, inspectUiPackFile, suggestedUiPackFileName } from "./lib/ui-pack.mjs";

const [command = "build", source, output] = process.argv.slice(2);

if (command === "inspect") {
  if (!source) throw new Error("Usage: build_ui_pack.mjs inspect <pack.vtp>");
  const inspected = await inspectUiPackFile(resolve(source));
  process.stdout.write(`${JSON.stringify(inspected, null, 2)}\n`);
} else {
  if (!source) throw new Error("Usage: build_ui_pack.mjs build <source-dir> [output.vtp]");
  const built = await buildUiPack(resolve(source));
  const target = resolve(output ?? `tmp/ui-packs/${suggestedUiPackFileName(built.manifest)}`);
  await mkdir(dirname(target), { recursive: true });
  await writeFile(target, built.buffer, { flag: "wx" });
  process.stdout.write(
    `${JSON.stringify({ output: target, id: built.manifest.id, version: built.manifest.version, bytes: built.buffer.length })}\n`,
  );
}
