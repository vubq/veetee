import { createHash, createPrivateKey, sign } from "node:crypto";
import { readFile } from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";

import { Injectable, ServiceUnavailableException } from "@nestjs/common";

import { canonicalizeRestrictedJcs } from "../artifacts/resource-manifest.service.js";
import { ControlPlaneStore } from "../store/control-plane.store.js";

const safeOpaqueId = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const safeDeviceId = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;
const safeResourceVersion = /^[A-Za-z0-9][A-Za-z0-9.+_-]{0,31}$/;
const wakeNetModelId = /^wn[A-Za-z0-9._-]{1,62}$/;
const maximumVersion = 2_147_483_647;

export interface DeviceConfigDetectorV1 {
  model_id: string;
  threshold_ppm: number;
  cooldown_ms: number;
}

export interface DeviceConfigInterruptV1 extends DeviceConfigDetectorV1 {
  enabled_while_speaking: boolean;
}

export interface SignedDeviceConfigV1 {
  schema_version: 1;
  device_id: string;
  version: number;
  wake_profile: {
    id: string;
    version: number;
    required_resource_version: string;
    activation: DeviceConfigDetectorV1;
    interrupt: DeviceConfigInterruptV1 | null;
    send_wake_audio: boolean;
  } | null;
  signature: {
    algorithm: "ed25519";
    key_id: string;
    security_epoch: number;
    value: string;
  };
}

export interface DeviceConfigRepresentation {
  body: SignedDeviceConfigV1;
  etag: string;
  canonicalBody: string;
}

@Injectable()
export class DeviceConfigService {
  constructor(private readonly store: ControlPlaneStore) {}

  async snapshot(deviceId: string): Promise<DeviceConfigRepresentation> {
    const source = await this.store.deviceConfigSource(deviceId);
    const keyId = process.env.VEETEE_RESOURCE_SIGNING_KEY_ID;
    const privateKeyPath = process.env.VEETEE_RESOURCE_SIGNING_PRIVATE_KEY ??
      (process.env.NODE_ENV === "production"
        ? undefined
        : developmentSigningKeyPath());
    const securityEpoch = Number(process.env.VEETEE_RESOURCE_SECURITY_EPOCH ?? "1");
    if (!keyId || !safeOpaqueId.test(keyId) || !privateKeyPath) {
      throw new ServiceUnavailableException("Device config release signer is not configured");
    }
    if (!Number.isSafeInteger(securityEpoch) || securityEpoch < 1 || securityEpoch > maximumVersion) {
      throw new ServiceUnavailableException("Device config security epoch is invalid");
    }

    const signatureMetadata = {
      algorithm: "ed25519" as const,
      key_id: keyId,
      security_epoch: securityEpoch,
    };
    const unsigned = {
      schema_version: 1 as const,
      device_id: string(source.deviceId, "deviceId", safeDeviceId),
      version: integer(source.version, "version", 1, maximumVersion),
      wake_profile: this.wakeProfile(source.state),
      signature: signatureMetadata,
    };
    const canonicalUnsigned = canonicalizeRestrictedJcs(unsigned);

    let signature: Buffer;
    try {
      const privateKey = createPrivateKey(await readFile(privateKeyPath));
      if (privateKey.asymmetricKeyType !== "ed25519") {
        throw new Error("release key is not Ed25519");
      }
      signature = sign(null, Buffer.from(canonicalUnsigned, "utf8"), privateKey);
    } catch {
      throw new ServiceUnavailableException("Device config release signer is unavailable");
    }
    if (signature.length !== 64) {
      throw new ServiceUnavailableException("Device config release signer returned an invalid signature");
    }

    const body: SignedDeviceConfigV1 = {
      ...unsigned,
      signature: {
        ...signatureMetadata,
        value: signature.toString("base64"),
      },
    };
    const canonicalBody = canonicalizeRestrictedJcs(body);
    const etag = `cfg1-${createHash("sha256").update(canonicalBody).digest("base64url")}`;
    return { body, etag, canonicalBody };
  }

  private wakeProfile(state: Record<string, unknown>): SignedDeviceConfigV1["wake_profile"] {
    if (state.wakeProfile === undefined || state.wakeProfile === null) return null;
    const profile = object(state.wakeProfile, "wakeProfile");
    const activation = object(profile.activation, "wakeProfile.activation");
    const interrupt = profile.interrupt === undefined || profile.interrupt === null
      ? null
      : object(profile.interrupt, "wakeProfile.interrupt");
    const activationConfig = this.detector(activation, "wakeProfile.activation");
    const interruptConfig = interrupt
      ? {
          ...this.detector(interrupt, "wakeProfile.interrupt"),
          enabled_while_speaking: stringArray(
            interrupt.allowedStates,
            "wakeProfile.interrupt.allowedStates",
          ).includes("speaking"),
        }
      : null;
    if (interruptConfig?.model_id === activationConfig.model_id) {
      invalid("wakeProfile.interrupt.detectorId");
    }

    return {
      id: string(profile.id, "wakeProfile.id", safeOpaqueId),
      version: integer(profile.version, "wakeProfile.version", 1, maximumVersion),
      required_resource_version: string(
        state.resourceBundleVersion,
        "resourceBundleVersion",
        safeResourceVersion,
      ),
      activation: activationConfig,
      interrupt: interruptConfig,
      send_wake_audio: optionalBoolean(
        profile.sendWakeAudio,
        "wakeProfile.sendWakeAudio",
      ),
    };
  }

  private detector(value: Record<string, unknown>, label: string): DeviceConfigDetectorV1 {
    const sensitivity = number(value.sensitivity, `${label}.sensitivity`, 0, 1);
    const thresholdPpm = sensitivity === 0
      ? 0
      : Math.min(999_900, Math.round(sensitivity * 1_000_000));
    if (thresholdPpm !== 0 && thresholdPpm < 400_000) {
      invalid(`${label}.sensitivity`);
    }
    return {
      model_id: string(value.detectorId, `${label}.detectorId`, wakeNetModelId),
      threshold_ppm: thresholdPpm,
      cooldown_ms: integer(value.cooldownMs, `${label}.cooldownMs`, 250, 10_000),
    };
  }
}

export function matchesDeviceConfigEtag(
  ifNoneMatch: string | string[] | undefined,
  etag: string,
): boolean {
  const value = Array.isArray(ifNoneMatch) ? ifNoneMatch.join(",") : ifNoneMatch;
  if (!value) return false;
  return value.split(",").some((candidate) => {
    let normalized = candidate.trim();
    if (normalized === "*") return true;
    if (normalized.startsWith("W/")) normalized = normalized.slice(2).trim();
    if (normalized.startsWith('"') && normalized.endsWith('"')) {
      normalized = normalized.slice(1, -1);
    }
    return normalized === etag;
  });
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid(label);
  return value as Record<string, unknown>;
}

function string(value: unknown, label: string, pattern: RegExp): string {
  if (typeof value !== "string" || !pattern.test(value) || value.includes("\0")) invalid(label);
  return value as string;
}

function integer(value: unknown, label: string, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum || Number(value) > maximum) {
    invalid(label);
  }
  return Number(value);
}

function number(value: unknown, label: string, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
    invalid(label);
  }
  return value as number;
}

function optionalBoolean(value: unknown, label: string): boolean {
  if (value === undefined) return false;
  if (typeof value !== "boolean") invalid(label);
  return value as boolean;
}

function stringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) invalid(label);
  return value as string[];
}

function invalid(label: string): never {
  throw new ServiceUnavailableException(`Desired device config ${label} is invalid`);
}

function developmentSigningKeyPath(): string {
  const cwd = process.cwd();
  const serverRoot = basename(cwd) === "manager-api" && basename(dirname(cwd)) === "apps"
    ? resolve(cwd, "../..")
    : basename(cwd) === "veetee-server"
      ? cwd
      : resolve(cwd, "veetee-server");
  return resolve(serverRoot, "data/signing/veetee-dev-release-2026-01.pem");
}
