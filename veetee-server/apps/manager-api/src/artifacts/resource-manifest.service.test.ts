import { generateKeyPairSync, sign } from "node:crypto";

import { afterEach, describe, expect, it, vi } from "vitest";

import type { ArtifactFilesService } from "./artifact-files.service.js";
import {
  canonicalizeRestrictedJcs,
  ResourceManifestService,
} from "./resource-manifest.service.js";

const payloadHash = "56fc71dda4bf4ebe6ed87359e3bda7eebef38dc0b8b01ce1203d2cd1dc212562";

function signedManifest() {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  const manifest = {
    manifest_version: 1,
    bundle_id: "veetee-resource-test",
    kind: "resource_bundle",
    version: "1.0.0",
    channel: "development",
    target: {
      board: "veetee-s3-n16r8",
      chip: "esp32s3",
      flash_bytes: 16_777_216,
      psram_bytes: 8_388_608,
    },
    compatibility: {
      min_firmware: "0.2.0",
      max_firmware_exclusive: "0.3.0",
      resource_abi: 1,
    },
    payload: {
      url: "http://192.168.1.20:8001/veetee/artifacts/stable/content",
      size: 125_943,
      sha256: payloadHash,
      content_type: "application/vnd.veetee.esp-sr-model-pack",
    },
    apply: { mode: "when_standby", requires_reboot: false, rollback_allowed: true },
    members: [
      {
        name: "speech/esp-sr-models",
        kind: "model_pack",
        runtime: "esp-sr",
        runtime_abi: 1,
        format_version: 1,
        sha256: payloadHash,
        bytes: 125_943,
      },
    ],
    created_at: "2026-07-22T03:45:00.000Z",
    signature: {
      algorithm: "ed25519",
      key_id: "test-release-key",
      security_epoch: 1,
      value: "",
    },
  };
  const unsigned = structuredClone(manifest);
  delete (unsigned.signature as { value?: string }).value;
  manifest.signature.value = sign(
    null,
    Buffer.from(canonicalizeRestrictedJcs(unsigned), "utf8"),
    privateKey,
  ).toString("base64");
  const spki = publicKey.export({ format: "der", type: "spki" });
  return { manifest, publicKeyHex: spki.subarray(-32).toString("hex") };
}

function signedFirmwareManifest() {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  const manifest = {
    manifest_version: 1,
    bundle_id: "firmware-0.4.0",
    kind: "firmware",
    version: "0.4.0",
    channel: "canary",
    target: {
      board: "veetee-s3-n16r8",
      chip: "esp32s3",
      flash_bytes: 16_777_216,
      psram_bytes: 8_388_608,
    },
    compatibility: { min_bootloader: "1.0.0", min_security_epoch: 2 },
    payload: {
      url: "http://192.168.1.20:8001/veetee/artifacts/fw-0.4.0/content",
      size: 1_542_144,
      sha256: payloadHash,
      content_type: "application/vnd.veetee.esp32s3-firmware",
    },
    apply: { mode: "when_standby", requires_reboot: true, rollback_allowed: true },
    created_at: "2026-07-23T03:45:00.000Z",
    signature: {
      algorithm: "ed25519",
      key_id: "test-release-key",
      security_epoch: 2,
      value: "",
    },
  };
  const unsigned = structuredClone(manifest);
  delete (unsigned.signature as { value?: string }).value;
  manifest.signature.value = sign(
    null,
    Buffer.from(canonicalizeRestrictedJcs(unsigned), "utf8"),
    privateKey,
  ).toString("base64");
  const spki = publicKey.export({ format: "der", type: "spki" });
  return { manifest, publicKeyHex: spki.subarray(-32).toString("hex") };
}

afterEach(() => vi.unstubAllEnvs());

describe("ResourceManifestService", () => {
  it("validates the stored payload, canonical signature and V1 data-only ABI", async () => {
    const fixture = signedManifest();
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_KEY_ID", "test-release-key");
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX", fixture.publicKeyHex);
    const files = {
      inspectRelease: vi.fn().mockResolvedValue({
        manifest: fixture.manifest,
        sizeBytes: 125_943,
        sha256: payloadHash,
      }),
    } as unknown as ArtifactFilesService;

    await expect(new ResourceManifestService(files).validate("stable")).resolves.toMatchObject({
      artifactId: "stable",
      kind: "resource_bundle",
      runtime: "esp-sr",
      runtimeAbi: 1,
      signatureKeyId: "test-release-key",
    });
  });

  it("rejects a signature after signed metadata is changed", async () => {
    const fixture = signedManifest();
    fixture.manifest.version = "1.0.1";
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_KEY_ID", "test-release-key");
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX", fixture.publicKeyHex);
    const files = {
      inspectRelease: vi.fn().mockResolvedValue({
        manifest: fixture.manifest,
        sizeBytes: 125_943,
        sha256: payloadHash,
      }),
    } as unknown as ArtifactFilesService;

    await expect(new ResourceManifestService(files).validate("stable")).rejects.toThrow(
      /signature verification/i,
    );
  });

  it("validates a signed ESP32-S3 executable image separately from resource bundles", async () => {
    const fixture = signedFirmwareManifest();
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_KEY_ID", "test-release-key");
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX", fixture.publicKeyHex);
    const files = {
      inspectRelease: vi.fn().mockResolvedValue({
        manifest: fixture.manifest,
        sizeBytes: 1_542_144,
        sha256: payloadHash,
      }),
      assertEsp32FirmwareImage: vi.fn().mockResolvedValue(undefined),
    } as unknown as ArtifactFilesService;

    await expect(new ResourceManifestService(files).validate("fw-0.4.0")).resolves.toMatchObject({
      artifactId: "fw-0.4.0",
      kind: "firmware",
      runtime: "esp-idf-image",
      securityEpoch: 2,
    });
    expect(files.assertEsp32FirmwareImage).toHaveBeenCalledWith("fw-0.4.0", 1_542_144);
  });
});
