import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

function walk(directory, extension) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? walk(path, extension) : path.endsWith(extension) ? [path] : [];
  });
}

const markdownFiles = [join(repoRoot, "README.md"), ...walk(join(repoRoot, "docs"), ".md")];
const errors = [];

for (const path of markdownFiles) {
  const content = readFileSync(path, "utf8");
  for (const match of content.matchAll(/```json\s*\n([\s\S]*?)```/g)) {
    try {
      JSON.parse(match[1]);
    } catch (error) {
      errors.push(`${relative(repoRoot, path)}: invalid fenced JSON: ${error.message}`);
    }
  }

  for (const match of content.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
    const rawTarget = match[1].trim();
    if (/^(?:https?:|mailto:|#)/.test(rawTarget)) continue;
    const target = rawTarget.split("#", 1)[0];
    if (target && !existsSync(resolve(dirname(path), target))) {
      errors.push(`${relative(repoRoot, path)}: missing link target ${rawTarget}`);
    }
  }
}

for (const path of walk(join(repoRoot, "veetee-server/packages/contracts/fixtures"), ".json")) {
  try {
    JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    errors.push(`${relative(repoRoot, path)}: invalid JSON: ${error.message}`);
  }
}

if (errors.length > 0) {
  process.stderr.write(`${errors.join("\n")}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(`Validated ${markdownFiles.length} Markdown files and contract fixtures.\n`);
}
