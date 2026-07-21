import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, realpath, type FileHandle } from "node:fs/promises";
import { resolve, sep } from "node:path";
import type { Readable } from "node:stream";

import {
  BadRequestException,
  ForbiddenException,
  HttpException,
  HttpStatus,
  Injectable,
  NotFoundException,
  PayloadTooLargeException,
} from "@nestjs/common";

const manifestFileName = "manifest.json";
const contentFileName = "content.bin";
const completeMarkerFileName = ".complete";
const maximumManifestBytes = 32 * 1024;
const defaultMaximumArtifactBytes = 64 * 1024 * 1024;

export interface ArtifactFileResponse {
  statusCode: 200 | 206;
  headers: Record<string, string>;
  stream: Readable;
}

export interface ByteRange {
  start: number;
  end: number;
}

export interface InspectedArtifactRelease {
  manifest: unknown;
  sizeBytes: number;
  sha256: string;
}

export class ArtifactRangeNotSatisfiableException extends HttpException {
  constructor(message: string, readonly artifactSize: number) {
    super(message, HttpStatus.REQUESTED_RANGE_NOT_SATISFIABLE);
  }
}

export function parseByteRange(value: string | undefined, size: number): ByteRange | undefined {
  if (value === undefined) return undefined;
  const match = /^bytes=(\d+)-(\d*)$/.exec(value);
  if (!match) {
    throw new ArtifactRangeNotSatisfiableException(
      "Only one byte range is supported",
      size,
    );
  }
  const start = Number(match[1]);
  const requestedEnd = match[2] ? Number(match[2]) : size - 1;
  if (
    !Number.isSafeInteger(start) ||
    !Number.isSafeInteger(requestedEnd) ||
    start < 0 ||
    start >= size ||
    requestedEnd < start
  ) {
    throw new ArtifactRangeNotSatisfiableException("Artifact byte range is invalid", size);
  }
  return { start, end: Math.min(requestedEnd, size - 1) };
}

@Injectable()
export class ArtifactFilesService {
  assertDeviceAccess(manifestId: string, desiredState: Record<string, unknown>): void {
    this.validateId(manifestId);
    const desiredManifestId = desiredState.resourceManifestId;
    const expected =
      typeof desiredManifestId === "string"
        ? desiredManifestId
        : process.env.VEETEE_RESOURCE_MANIFEST_ID ?? this.manifestIdFromUrl();
    if (!expected || expected !== manifestId) {
      throw new ForbiddenException("Artifact is outside the device rollout scope");
    }
  }

  async openManifest(manifestId: string): Promise<ArtifactFileResponse> {
    const file = await this.openArtifactFile(manifestId, manifestFileName);
    const stat = await file.stat();
    if (!stat.isFile() || stat.size <= 0) {
      await file.close();
      throw new NotFoundException("Artifact manifest not found");
    }
    if (stat.size > maximumManifestBytes) {
      await file.close();
      throw new PayloadTooLargeException("Artifact manifest exceeds 32 KiB");
    }
    return {
      statusCode: 200,
      headers: {
        "Cache-Control": "private, no-cache",
        "Content-Length": String(stat.size),
        "Content-Type": "application/json; charset=utf-8",
        ETag: `\"${manifestId}-manifest-${stat.size}\"`,
      },
      stream: file.createReadStream({ autoClose: true }),
    };
  }

  async inspectRelease(artifactId: string): Promise<InspectedArtifactRelease> {
    const manifestFile = await this.openArtifactFile(artifactId, manifestFileName);
    let manifestBytes: Buffer;
    try {
      const stat = await manifestFile.stat();
      if (!stat.isFile() || stat.size <= 0 || stat.size > maximumManifestBytes) {
        throw new Error("Artifact manifest is invalid");
      }
      manifestBytes = await manifestFile.readFile();
    } finally {
      await manifestFile.close();
    }

    const contentFile = await this.openArtifactFile(artifactId, contentFileName);
    const stat = await contentFile.stat();
    if (!stat.isFile() || stat.size <= 0 || stat.size > this.maximumArtifactBytes()) {
      await contentFile.close();
      throw new PayloadTooLargeException("Artifact content is invalid or too large");
    }
    const hash = createHash("sha256");
    for await (const chunk of contentFile.createReadStream({ autoClose: true })) {
      hash.update(chunk);
    }
    let manifest: unknown;
    try {
      manifest = JSON.parse(manifestBytes.toString("utf8"));
    } catch {
      throw new BadRequestException("Artifact manifest is not valid JSON");
    }
    return { manifest, sizeBytes: stat.size, sha256: hash.digest("hex") };
  }

  async openContent(
    artifactId: string,
    rangeHeader: string | undefined,
  ): Promise<ArtifactFileResponse> {
    const file = await this.openArtifactFile(artifactId, contentFileName);
    const stat = await file.stat();
    const maximumBytes = this.maximumArtifactBytes();
    if (!stat.isFile() || stat.size <= 0) {
      await file.close();
      throw new NotFoundException("Artifact content not found");
    }
    if (stat.size > maximumBytes) {
      await file.close();
      throw new PayloadTooLargeException("Artifact exceeds the configured size limit");
    }

    let range: ByteRange | undefined;
    try {
      range = parseByteRange(rangeHeader, stat.size);
    } catch (error) {
      await file.close();
      throw error;
    }
    const start = range?.start ?? 0;
    const end = range?.end ?? stat.size - 1;
    const headers: Record<string, string> = {
      "Accept-Ranges": "bytes",
      "Cache-Control": "private, max-age=31536000, immutable",
      "Content-Length": String(end - start + 1),
      "Content-Type": "application/octet-stream",
      ETag: `\"${artifactId}-content-${stat.size}\"`,
    };
    if (range) headers["Content-Range"] = `bytes ${start}-${end}/${stat.size}`;
    return {
      statusCode: range ? 206 : 200,
      headers,
      stream: file.createReadStream({ start, end, autoClose: true }),
    };
  }

  private async openArtifactFile(artifactId: string, fileName: string): Promise<FileHandle> {
    this.validateId(artifactId);
    try {
      const configuredRoot = resolve(process.env.VEETEE_ARTIFACT_ROOT ?? "data/artifacts");
      const root = await realpath(configuredRoot);
      const directory = resolve(configuredRoot, artifactId);
      const directoryStat = await lstat(directory);
      if (!directoryStat.isDirectory() || directoryStat.isSymbolicLink()) {
        throw new Error("Artifact directory is not immutable storage");
      }
      const artifactRoot = await realpath(directory);
      if (!artifactRoot.startsWith(`${root}${sep}`)) {
        throw new Error("Artifact directory escaped its configured root");
      }
      await this.assertComplete(artifactRoot);
      const path = resolve(artifactRoot, fileName);
      return await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    } catch {
      throw new NotFoundException("Artifact not found");
    }
  }

  private async assertComplete(artifactRoot: string): Promise<void> {
    const marker = await open(
      resolve(artifactRoot, completeMarkerFileName),
      constants.O_RDONLY | constants.O_NOFOLLOW,
    );
    try {
      const stat = await marker.stat();
      if (!stat.isFile() || stat.size <= 0) {
        throw new Error("Artifact release is incomplete");
      }
    } finally {
      await marker.close();
    }
  }

  private validateId(value: string): void {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value)) {
      throw new NotFoundException("Artifact not found");
    }
  }

  private maximumArtifactBytes(): number {
    const configured = Number(
      process.env.VEETEE_MAX_RESOURCE_ARTIFACT_BYTES ?? defaultMaximumArtifactBytes,
    );
    if (!Number.isSafeInteger(configured) || configured <= 0) {
      throw new Error("VEETEE_MAX_RESOURCE_ARTIFACT_BYTES must be a positive integer");
    }
    return configured;
  }

  private manifestIdFromUrl(): string | undefined {
    const value = process.env.VEETEE_RESOURCE_MANIFEST_URL;
    if (!value) return undefined;
    const parts = new URL(value).pathname.split("/").filter(Boolean);
    return parts.at(-1);
  }
}
