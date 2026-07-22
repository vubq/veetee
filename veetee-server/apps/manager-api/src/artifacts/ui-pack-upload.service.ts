import { randomUUID } from "node:crypto";
import { createWriteStream } from "node:fs";
import { access, mkdir, rename, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";

import {
  BadRequestException,
  ConflictException,
  Injectable,
  PayloadTooLargeException,
  ServiceUnavailableException,
} from "@nestjs/common";

import { sha256File, signResourceManifest } from "../../../../scripts/lib/resource-manifest.mjs";
import {
  buildUiPack,
  inspectUiPackFile,
  suggestedUiPackFileName,
  UI_PACK_MAX_BYTES,
} from "../../../../scripts/lib/ui-pack.mjs";
import type { Principal } from "../auth/auth.types.js";
import { ResourceCatalogService, type ArtifactRecord } from "./resource-catalog.service.js";

interface UploadContext {
  principal: Principal;
  requestId: string;
}

const standardThemeIds = ["signal", "monolith", "quiet"] as const;
export type StandardUiThemeId = (typeof standardThemeIds)[number];

class SizeLimiter extends Transform {
  private bytes = 0;

  override _transform(
    chunk: Buffer,
    encoding: BufferEncoding,
    callback: (error?: Error | null, data?: Buffer) => void,
  ): void {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk, encoding);
    this.bytes += buffer.length;
    if (this.bytes > UI_PACK_MAX_BYTES) {
      callback(new PayloadTooLargeException("UI Pack exceeds 2 MiB"));
      return;
    }
    callback(null, buffer);
  }
}

@Injectable()
export class UiPackUploadService {
  constructor(private readonly catalog: ResourceCatalogService) {}

  async stageStandard(
    themeId: string,
    context: UploadContext,
  ): Promise<ArtifactRecord> {
    if (!standardThemeIds.includes(themeId as StandardUiThemeId)) {
      throw new BadRequestException("Unknown standard UI theme");
    }
    const sourceRoot = resolve(
      process.env.VEETEE_UI_PACK_SOURCE_ROOT ?? resolve(process.cwd(), "../../ui-packs"),
    );
    try {
      const built = await buildUiPack(resolve(sourceRoot, themeId));
      return await this.stage(
        Readable.from([built.buffer]),
        suggestedUiPackFileName(built.manifest),
        context,
      );
    } catch (error) {
      if (
        error instanceof BadRequestException ||
        error instanceof ConflictException ||
        error instanceof PayloadTooLargeException ||
        error instanceof ServiceUnavailableException
      ) {
        throw error;
      }
      throw new ServiceUnavailableException(
        error instanceof Error ? `Standard UI Pack is unavailable: ${error.message}` : "Standard UI Pack is unavailable",
      );
    }
  }

  async stage(
    body: unknown,
    fileName: string | undefined,
    context: UploadContext,
  ): Promise<ArtifactRecord> {
    if (!(body instanceof Readable)) {
      throw new BadRequestException("UI Pack request body must be a binary stream");
    }
    if (fileName && (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(fileName) || !/\.(?:vtp|bin)$/i.test(fileName))) {
      throw new BadRequestException("UI Pack file name is invalid");
    }
    const root = resolve(process.env.VEETEE_ARTIFACT_ROOT ?? "data/artifacts");
    const quarantine = resolve(root, ".quarantine", randomUUID());
    const contentPath = resolve(quarantine, "content.bin");
    await mkdir(quarantine, { recursive: true, mode: 0o700 });

    try {
      await pipeline(body, new SizeLimiter(), createWriteStream(contentPath, { flags: "wx", mode: 0o600 }));
      const pack = await inspectUiPackFile(contentPath);
      const artifactId = pack.manifest.id;
      const output = resolve(root, artifactId);
      if (!output.startsWith(`${root}/`)) throw new BadRequestException("UI Pack id escaped storage");

      try {
        await access(output);
        await rm(quarantine, { recursive: true, force: true });
        return this.catalog.registerArtifact(
          artifactId,
          pack.manifest.license,
          "not_run",
          context,
        );
      } catch (error) {
        if (error && typeof error === "object" && "code" in error && error.code !== "ENOENT") {
          throw error;
        }
      }

      const privateKeyPath =
        process.env.VEETEE_RESOURCE_SIGNING_PRIVATE_KEY ??
        (process.env.NODE_ENV === "production"
          ? undefined
          : resolve(root, "..", "signing", "veetee-dev-release-2026-01.pem"));
      const keyId = process.env.VEETEE_RESOURCE_SIGNING_KEY_ID;
      const managerUrl = process.env.VEETEE_MANAGER_PUBLIC_URL;
      const securityEpoch = Number(process.env.VEETEE_RESOURCE_SECURITY_EPOCH ?? "1");
      if (!privateKeyPath || !keyId || !managerUrl) {
        throw new ServiceUnavailableException("UI Pack release signer is not configured");
      }
      if (!Number.isSafeInteger(securityEpoch) || securityEpoch <= 0) {
        throw new ServiceUnavailableException("UI Pack security epoch is invalid");
      }
      const origin = new URL(managerUrl);
      if (
        !["http:", "https:"].includes(origin.protocol) ||
        origin.username ||
        origin.password ||
        origin.pathname !== "/" ||
        origin.search ||
        origin.hash
      ) {
        throw new ServiceUnavailableException("Manager public URL must be an HTTP(S) origin");
      }
      const sha256 = await sha256File(contentPath);
      const base = origin.toString().replace(/\/$/, "");
      const externalManifest = {
        manifest_version: 1,
        bundle_id: artifactId,
        kind: "ui_pack",
        version: pack.manifest.version,
        channel: pack.manifest.channel,
        target: {
          board: "veetee-s3-n16r8",
          chip: "esp32s3",
          flash_bytes: 16 * 1024 * 1024,
          psram_bytes: 8 * 1024 * 1024,
        },
        compatibility: {
          min_firmware: pack.manifest.compatibility.min_firmware,
          max_firmware_exclusive: pack.manifest.compatibility.max_firmware_exclusive,
          resource_abi: 2,
          ui_abi: 1,
        },
        payload: {
          url: `${base}/veetee/artifacts/${encodeURIComponent(artifactId)}/content`,
          size: pack.sizeBytes,
          sha256,
          content_type: "application/vnd.veetee.ui-pack",
        },
        apply: {
          mode: "when_standby",
          requires_reboot: false,
          rollback_allowed: true,
        },
        members: [
          {
            name: "display/ui-pack",
            kind: "display_assets",
            runtime: "veetee-ui",
            runtime_abi: 1,
            format_version: 1,
            sha256,
            bytes: pack.sizeBytes,
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
      const signed = await signResourceManifest(externalManifest, resolve(privateKeyPath));
      await writeFile(resolve(quarantine, "manifest.json"), `${JSON.stringify(signed, null, 2)}\n`, {
        flag: "wx",
        mode: 0o600,
      });
      await writeFile(resolve(quarantine, ".complete"), `${sha256}  content.bin\n`, {
        flag: "wx",
        mode: 0o600,
      });
      await rename(quarantine, output).catch((error: unknown) => {
        if (error && typeof error === "object" && "code" in error && error.code === "EEXIST") {
          throw new ConflictException("Immutable UI Pack id already exists");
        }
        throw error;
      });
      return await this.catalog.registerArtifact(
        artifactId,
        pack.manifest.license,
        "not_run",
        context,
      );
    } catch (error) {
      await rm(quarantine, { recursive: true, force: true });
      if (
        error instanceof BadRequestException ||
        error instanceof ConflictException ||
        error instanceof PayloadTooLargeException ||
        error instanceof ServiceUnavailableException
      ) {
        throw error;
      }
      throw new BadRequestException(
        error instanceof Error ? error.message : "UI Pack staging failed",
      );
    }
  }
}
