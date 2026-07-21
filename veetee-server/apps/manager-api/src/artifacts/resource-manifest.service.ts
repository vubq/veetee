import { createPublicKey, verify } from "node:crypto";

import { BadRequestException, Injectable } from "@nestjs/common";

import { ArtifactFilesService } from "./artifact-files.service.js";

const ed25519SpkiPrefix = Buffer.from("302a300506032b6570032100", "hex");
const safeId = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const semver = /^\d+\.\d+\.\d+$/;
const sha256 = /^[a-f0-9]{64}$/;
const allowedChannels = new Set(["development", "canary", "stable"]);

export interface ValidatedResourceManifest {
  artifactId: string;
  kind: "resource_bundle";
  version: string;
  channel: string;
  sizeBytes: number;
  sha256: string;
  contentType: string;
  runtime: string;
  runtimeAbi: number;
  board: string;
  minFirmware: string;
  maxFirmware: string;
  signatureKeyId: string;
  securityEpoch: number;
  manifest: Record<string, unknown>;
}

@Injectable()
export class ResourceManifestService {
  constructor(private readonly files: ArtifactFilesService) {}

  async validate(artifactId: string): Promise<ValidatedResourceManifest> {
    if (!safeId.test(artifactId)) throw new BadRequestException("Artifact id is invalid");
    const inspected = await this.files.inspectRelease(artifactId);
    const manifest = object(inspected.manifest, "manifest");
    integer(manifest.manifest_version, "manifest_version", 1, 1);
    const bundleId = string(manifest.bundle_id, "bundle_id", safeId);
    if (bundleId.length < 1) throw new BadRequestException("bundle_id is invalid");
    const kind = string(manifest.kind, "kind");
    if (kind !== "resource_bundle") {
      throw new BadRequestException("Only resource_bundle manifests are supported in ABI V1");
    }
    const version = string(manifest.version, "version", semver);
    const channel = string(manifest.channel, "channel");
    if (!allowedChannels.has(channel)) throw new BadRequestException("Artifact channel is invalid");

    const target = object(manifest.target, "target");
    const board = string(target.board, "target.board", safeId);
    if (board !== "veetee-s3-n16r8" || target.chip !== "esp32s3") {
      throw new BadRequestException("Artifact target is not the Veetee ESP32-S3 board");
    }
    integer(target.flash_bytes, "target.flash_bytes", 16_777_216, 16_777_216);
    integer(target.psram_bytes, "target.psram_bytes", 8_388_608, 8_388_608);

    const compatibility = object(manifest.compatibility, "compatibility");
    const minFirmware = string(compatibility.min_firmware, "compatibility.min_firmware", semver);
    const maxFirmware = string(
      compatibility.max_firmware_exclusive,
      "compatibility.max_firmware_exclusive",
      semver,
    );
    integer(compatibility.resource_abi, "compatibility.resource_abi", 1, 1);

    const payload = object(manifest.payload, "payload");
    const payloadUrl = new URL(string(payload.url, "payload.url"));
    if (
      !["http:", "https:"].includes(payloadUrl.protocol) ||
      payloadUrl.username ||
      payloadUrl.password ||
      payloadUrl.pathname !== `/veetee/artifacts/${encodeURIComponent(artifactId)}/content`
    ) {
      throw new BadRequestException("Artifact payload URL is outside the canonical route");
    }
    const sizeBytes = integer(payload.size, "payload.size", 1, 4 * 1024 * 1024);
    const payloadHash = string(payload.sha256, "payload.sha256", sha256);
    if (sizeBytes !== inspected.sizeBytes || payloadHash !== inspected.sha256) {
      throw new BadRequestException("Artifact payload size or SHA-256 does not match storage");
    }
    const contentType = string(payload.content_type, "payload.content_type");
    if (contentType !== "application/vnd.veetee.esp-sr-model-pack") {
      throw new BadRequestException("Artifact content type is unsupported");
    }

    const members = array(manifest.members, "members");
    if (members.length !== 1) {
      throw new BadRequestException("Resource ABI V1 requires exactly one member");
    }
    const member = object(members[0], "members[0]");
    if (member.kind !== "model_pack") {
      throw new BadRequestException("Resource ABI V1 only accepts a data model_pack");
    }
    const runtime = string(member.runtime, "members[0].runtime", safeId);
    const runtimeAbi = integer(member.runtime_abi, "members[0].runtime_abi", 1, 1);
    integer(member.format_version, "members[0].format_version", 1, 1);
    if (member.bytes !== sizeBytes || member.sha256 !== payloadHash) {
      throw new BadRequestException("Artifact member does not match the signed payload");
    }

    const signature = object(manifest.signature, "signature");
    if (signature.algorithm !== "ed25519") {
      throw new BadRequestException("Artifact signature algorithm is unsupported");
    }
    const signatureKeyId = string(signature.key_id, "signature.key_id", safeId);
    const expectedKeyId = process.env.VEETEE_RESOURCE_SIGNING_KEY_ID;
    if (!expectedKeyId || signatureKeyId !== expectedKeyId) {
      throw new BadRequestException("Artifact signing key is not trusted");
    }
    const securityEpoch = integer(
      signature.security_epoch,
      "signature.security_epoch",
      1,
      2_147_483_647,
    );
    const signatureBytes = Buffer.from(string(signature.value, "signature.value"), "base64");
    if (signatureBytes.length !== 64) {
      throw new BadRequestException("Artifact signature must contain 64 bytes");
    }
    const publicKeyHex = process.env.VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX ?? "";
    if (!/^[a-fA-F0-9]{64}$/.test(publicKeyHex)) {
      throw new BadRequestException("Artifact signing public key is not configured");
    }
    const unsigned = structuredClone(manifest);
    const unsignedSignature = object(unsigned.signature, "signature");
    delete unsignedSignature.value;
    const canonical = canonicalizeRestrictedJcs(unsigned);
    const publicKey = createPublicKey({
      key: Buffer.concat([ed25519SpkiPrefix, Buffer.from(publicKeyHex, "hex")]),
      format: "der",
      type: "spki",
    });
    if (!verify(null, Buffer.from(canonical, "utf8"), publicKey, signatureBytes)) {
      throw new BadRequestException("Artifact signature verification failed");
    }

    return {
      artifactId,
      kind,
      version,
      channel,
      sizeBytes,
      sha256: payloadHash,
      contentType,
      runtime,
      runtimeAbi,
      board,
      minFirmware,
      maxFirmware,
      signatureKeyId,
      securityEpoch,
      manifest,
    };
  }
}

export function canonicalizeRestrictedJcs(value: unknown): string {
  if (value === null || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "string") {
    if (value.includes("\0")) throw new BadRequestException("Manifest contains NUL");
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      throw new BadRequestException("Manifest numbers must be exact integers");
    }
    return String(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalizeRestrictedJcs(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record).sort();
    if (keys.some((key) => !/^[\x20-\x7e]+$/.test(key))) {
      throw new BadRequestException("Manifest property names must be printable ASCII");
    }
    return `{${keys
      .map((key) => `${JSON.stringify(key)}:${canonicalizeRestrictedJcs(record[key])}`)
      .join(",")}}`;
  }
  throw new BadRequestException("Manifest contains an unsupported JSON value");
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new BadRequestException(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new BadRequestException(`${label} must be an array`);
  return value;
}

function string(value: unknown, label: string, pattern?: RegExp): string {
  if (typeof value !== "string" || !value || (pattern && !pattern.test(value))) {
    throw new BadRequestException(`${label} is invalid`);
  }
  return value;
}

function integer(value: unknown, label: string, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum || Number(value) > maximum) {
    throw new BadRequestException(`${label} is invalid`);
  }
  return Number(value);
}
