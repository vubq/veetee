import { copyFile, mkdir, open, stat, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import { sha256File, signResourceManifest } from "./lib/resource-manifest.mjs";

const maximumSlotBytes = 2 * 1024 * 1024;

function parseArguments(values) {
  const result = {};
  for (let index = 0; index < values.length; index += 2) {
    const name = values[index];
    const value = values[index + 1];
    if (!name?.startsWith("--") || value === undefined) {
      throw new Error(`Invalid argument near ${name ?? "end of command"}`);
    }
    result[name.slice(2)] = value;
  }
  return result;
}

function required(options, name, environmentName) {
  const value = options[name] ?? process.env[environmentName];
  if (!value) throw new Error(`${name} is required (--${name} or ${environmentName})`);
  return value;
}

function positiveInteger(value, name) {
  const result = Number(value);
  if (!Number.isSafeInteger(result) || result <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return result;
}

function validateId(value, name) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value)) {
    throw new Error(`${name} must be a safe 1-64 character identifier`);
  }
  return value;
}

function semver(value, name) {
  if (!/^\d+\.\d+\.\d+$/.test(value)) throw new Error(`${name} must be x.y.z`);
  return value;
}

async function ensureNewDirectory(path) {
  try {
    await stat(path);
    throw new Error(`Refusing to overwrite immutable release directory: ${path}`);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  await mkdir(path, { recursive: false });
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const input = resolve(required(options, "input", "VEETEE_RESOURCE_INPUT"));
  const outputRoot = resolve(options["output-root"] ?? process.env.VEETEE_ARTIFACT_ROOT ?? "data/artifacts");
  const artifactId = validateId(required(options, "artifact-id", "VEETEE_RESOURCE_MANIFEST_ID"), "artifact-id");
  const bundleId = validateId(options["bundle-id"] ?? artifactId, "bundle-id");
  const version = semver(required(options, "version", "VEETEE_RESOURCE_VERSION"), "version");
  const publicBaseUrl = new URL(required(options, "public-base-url", "VEETEE_MANAGER_PUBLIC_URL"));
  if (!(["http:", "https:"].includes(publicBaseUrl.protocol))) {
    throw new Error("public-base-url must use HTTP or HTTPS");
  }
  if (
    publicBaseUrl.username ||
    publicBaseUrl.password ||
    publicBaseUrl.pathname !== "/" ||
    publicBaseUrl.search ||
    publicBaseUrl.hash
  ) {
    throw new Error("public-base-url must be an HTTP(S) origin without credentials or a path");
  }
  const privateKey = resolve(required(options, "private-key", "VEETEE_RESOURCE_SIGNING_PRIVATE_KEY"));
  const keyId = validateId(
    options["key-id"] ?? process.env.VEETEE_RESOURCE_SIGNING_KEY_ID ?? "veetee-dev-release-2026-01",
    "key-id",
  );
  const securityEpoch = positiveInteger(
    options["security-epoch"] ?? process.env.VEETEE_RESOURCE_SECURITY_EPOCH ?? "1",
    "security-epoch",
  );
  const inputStat = await stat(input);
  if (!inputStat.isFile() || inputStat.size <= 0 || inputStat.size > maximumSlotBytes) {
    throw new Error(`Input must be a non-empty file no larger than ${maximumSlotBytes} bytes`);
  }
  const payloadHash = await sha256File(input);
  const base = publicBaseUrl.toString().replace(/\/$/, "");
  const manifest = {
    manifest_version: 1,
    bundle_id: bundleId,
    kind: "resource_bundle",
    version,
    channel: options.channel ?? "development",
    target: {
      board: "veetee-s3-n16r8",
      chip: "esp32s3",
      flash_bytes: 16 * 1024 * 1024,
      psram_bytes: 8 * 1024 * 1024,
    },
    compatibility: {
      min_firmware: semver(options["min-firmware"] ?? "0.3.0", "min-firmware"),
      max_firmware_exclusive: semver(
        options["max-firmware-exclusive"] ?? "0.4.0",
        "max-firmware-exclusive",
      ),
      resource_abi: 1,
    },
    payload: {
      url: `${base}/veetee/artifacts/${encodeURIComponent(artifactId)}/content`,
      size: inputStat.size,
      sha256: payloadHash,
      content_type: "application/vnd.veetee.esp-sr-model-pack",
    },
    apply: {
      mode: "when_standby",
      requires_reboot: false,
      rollback_allowed: true,
    },
    members: [
      {
        name: "speech/esp-sr-models",
        kind: "model_pack",
        runtime: "esp-sr",
        runtime_abi: 1,
        format_version: 1,
        sha256: payloadHash,
        bytes: inputStat.size,
      },
    ],
    created_at: new Date().toISOString(),
    signature: {
      algorithm: "ed25519",
      key_id: keyId,
      security_epoch: securityEpoch,
      value: "",
    },
  };
  if (!["development", "canary", "stable"].includes(manifest.channel)) {
    throw new Error("channel must be development, canary or stable");
  }
  const signed = await signResourceManifest(manifest, privateKey);
  const output = resolve(outputRoot, artifactId);
  await mkdir(dirname(output), { recursive: true });
  await ensureNewDirectory(output);
  await copyFile(input, resolve(output, "content.bin"));
  await writeFile(resolve(output, "manifest.json"), `${JSON.stringify(signed, null, 2)}\n`, {
    flag: "wx",
    mode: 0o644,
  });
  const marker = await open(resolve(output, ".complete"), "wx", 0o644);
  await marker.writeFile(`${payloadHash}  content.bin\n`);
  await marker.close();
  process.stdout.write(
    `${JSON.stringify({ artifactId, version, bytes: inputStat.size, sha256: payloadHash, output })}\n`,
  );
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
