import { copyFile, mkdir, open, stat, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import { sha256File, signResourceManifest } from "./lib/resource-manifest.mjs";
import { inspectEsp32AppImage } from "./lib/firmware-image.mjs";

const maximumSlotBytes = 0x3a0000;

function args(values) {
  const output = {};
  for (let index = 0; index < values.length; index += 2) {
    const name = values[index];
    const value = values[index + 1];
    if (!name?.startsWith("--") || value === undefined) throw new Error(`Invalid argument near ${name ?? "end"}`);
    output[name.slice(2)] = value;
  }
  return output;
}
function required(options, name, env) {
  const value = options[name] ?? process.env[env];
  if (!value) throw new Error(`${name} is required (--${name} or ${env})`);
  return value;
}
function id(value, name) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value)) throw new Error(`${name} is invalid`);
  return value;
}
function semver(value, name) {
  if (!/^\d+\.\d+\.\d+$/.test(value)) throw new Error(`${name} must be x.y.z`);
  return value;
}
async function newDirectory(path) {
  try {
    await stat(path);
    throw new Error(`Refusing to overwrite immutable release directory: ${path}`);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  await mkdir(path, { recursive: false });
}

async function main() {
  const options = args(process.argv.slice(2));
  const input = resolve(required(options, "input", "VEETEE_FIRMWARE_INPUT"));
  const artifactId = id(required(options, "artifact-id", "VEETEE_FIRMWARE_MANIFEST_ID"), "artifact-id");
  const version = semver(required(options, "version", "VEETEE_FIRMWARE_VERSION"), "version");
  const channel = options.channel ?? "canary";
  if (!["development", "canary", "stable"].includes(channel)) throw new Error("channel is invalid");
  const baseUrl = new URL(required(options, "public-base-url", "VEETEE_MANAGER_PUBLIC_URL"));
  if (!["http:", "https:"].includes(baseUrl.protocol) || baseUrl.username || baseUrl.password ||
      baseUrl.pathname !== "/" || baseUrl.search || baseUrl.hash) {
    throw new Error("public-base-url must be an HTTP(S) origin");
  }
  const privateKey = resolve(required(options, "private-key", "VEETEE_RESOURCE_SIGNING_PRIVATE_KEY"));
  const outputRoot = resolve(options["output-root"] ?? process.env.VEETEE_ARTIFACT_ROOT ?? "data/artifacts");
  const keyId = id(options["key-id"] ?? process.env.VEETEE_RESOURCE_SIGNING_KEY_ID ?? "veetee-dev-release-2026-01", "key-id");
  const securityEpoch = Number(options["security-epoch"] ?? process.env.VEETEE_RESOURCE_SECURITY_EPOCH ?? "1");
  if (!Number.isSafeInteger(securityEpoch) || securityEpoch < 1) throw new Error("security-epoch is invalid");
  const inputStat = await stat(input);
  if (!inputStat.isFile() || inputStat.size <= 24 || inputStat.size > maximumSlotBytes) {
    throw new Error(`Firmware input must be a non-empty ESP image no larger than ${maximumSlotBytes} bytes`);
  }
  const image = await readFile(input, { encoding: null, flag: "r" });
  const inspectedImage = inspectEsp32AppImage(image);
  if (inspectedImage.releaseVersion !== version) {
    throw new Error(
      `Firmware image version ${inspectedImage.releaseVersion} does not match manifest version ${version}`,
    );
  }
  const hash = await sha256File(input);
  const base = baseUrl.toString().replace(/\/$/, "");
  const manifest = {
    manifest_version: 1,
    bundle_id: `firmware-${version}`,
    kind: "firmware",
    version,
    channel,
    target: { board: "veetee-s3-n16r8", chip: "esp32s3", flash_bytes: 16 * 1024 * 1024, psram_bytes: 8 * 1024 * 1024 },
    compatibility: {
      min_bootloader: semver(options["min-bootloader"] ?? "1.0.0", "min-bootloader"),
      min_security_epoch: securityEpoch,
    },
    payload: {
      url: `${base}/veetee/artifacts/${encodeURIComponent(artifactId)}/content`,
      size: inputStat.size,
      sha256: hash,
      content_type: "application/vnd.veetee.esp32s3-firmware",
    },
    apply: { mode: "when_standby", requires_reboot: true, rollback_allowed: true },
    created_at: new Date().toISOString(),
    signature: { algorithm: "ed25519", key_id: keyId, security_epoch: securityEpoch, value: "" },
  };
  const signed = await signResourceManifest(manifest, privateKey);
  const output = resolve(outputRoot, artifactId);
  await mkdir(dirname(output), { recursive: true });
  await newDirectory(output);
  await copyFile(input, resolve(output, "content.bin"));
  await writeFile(resolve(output, "manifest.json"), `${JSON.stringify(signed, null, 2)}\n`, { flag: "wx", mode: 0o644 });
  const marker = await open(resolve(output, ".complete"), "wx", 0o644);
  await marker.writeFile(`${hash}  content.bin\n`);
  await marker.close();
  process.stdout.write(`${JSON.stringify({ artifactId, version, channel, bytes: inputStat.size, sha256: hash, output })}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
