import { createHash, createPrivateKey, sign } from "node:crypto";
import { createReadStream } from "node:fs";
import { readFile } from "node:fs/promises";

const maximumExactInteger = Number.MAX_SAFE_INTEGER;

export function canonicalizeRestrictedJcs(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || Math.abs(value) > maximumExactInteger) {
      throw new TypeError("Resource manifests only support exact JSON integers");
    }
    return String(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalizeRestrictedJcs(item)).join(",")}]`;
  }
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    for (const key of keys) {
      if (!/^[\x20-\x7e]+$/.test(key)) {
        throw new TypeError("Resource manifest property names must be printable ASCII");
      }
    }
    return `{${keys
      .map((key) => `${JSON.stringify(key)}:${canonicalizeRestrictedJcs(value[key])}`)
      .join(",")}}`;
  }
  throw new TypeError(`Unsupported manifest value: ${typeof value}`);
}

export async function sha256File(path) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest("hex");
}

export async function signResourceManifest(manifest, privateKeyPath) {
  const privateKeyPem = await readFile(privateKeyPath);
  const privateKey = createPrivateKey(privateKeyPem);
  const unsigned = structuredClone(manifest);
  delete unsigned.signature.value;
  const canonical = canonicalizeRestrictedJcs(unsigned);
  const signature = sign(null, Buffer.from(canonical, "utf8"), privateKey);
  if (signature.length !== 64) throw new Error("Ed25519 signature must contain 64 bytes");
  return {
    ...manifest,
    signature: { ...manifest.signature, value: signature.toString("base64") },
  };
}
