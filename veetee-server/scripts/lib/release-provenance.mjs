import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { basename } from "node:path";

export function sha256Buffer(value) {
  return createHash("sha256").update(value).digest("hex");
}
export function repositoryProvenance(cwd = process.cwd()) {
  try {
    const commit = execFileSync("git", ["rev-parse", "HEAD"], {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    const dirty = execFileSync("git", ["status", "--porcelain", "--untracked-files=no"], {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim().length > 0;
    return { commit, dirty };
  } catch {
    return { commit: null, dirty: null };
  }
}

export function buildReleaseMetadata({
  artifactId,
  kind,
  version,
  channel,
  contentFileName,
  contentBytes,
  contentSha256,
  manifestText,
  generatedAt = new Date().toISOString(),
  sourceFileName,
  repository,
  nodeVersion = process.version,
}) {
  const manifestSha256 = sha256Buffer(Buffer.from(manifestText, "utf8"));
  const provenance = {
    schema_version: 1,
    artifact_id: artifactId,
    kind,
    version,
    channel,
    generated_at: generatedAt,
    source: {
      file_name: basename(sourceFileName ?? contentFileName),
      content_file: contentFileName,
    },
    content: {
      bytes: contentBytes,
      sha256: contentSha256,
    },
    manifest: {
      file_name: "manifest.json",
      sha256: manifestSha256,
    },
    build: {
      repository: repository ?? { commit: null, dirty: null },
      node_version: nodeVersion,
      release_script: "veetee-release-v1",
    },
  };
  const sbom = {
    spdxVersion: "SPDX-2.3",
    dataLicense: "CC0-1.0",
    SPDXID: "SPDXRef-DOCUMENT",
    name: `${artifactId}-sbom`,
    documentNamespace: `https://schemas.veetee.local/provenance/${artifactId}/${version}/${contentSha256.slice(0, 16)}`,
    creationInfo: {
      created: generatedAt,
      creators: ["Tool: veetee-release-v1"],
    },
    packages: [
      {
        SPDXID: "SPDXRef-Package-VeeteeArtifact",
        name: `${artifactId}:${kind}`,
        versionInfo: version,
        downloadLocation: "NOASSERTION",
        filesAnalyzed: false,
        licenseConcluded: "NOASSERTION",
        licenseDeclared: "NOASSERTION",
        checksums: [{ algorithm: "SHA256", checksumValue: contentSha256 }],
        supplier: "NOASSERTION",
        externalRefs: [],
      },
    ],
    files: [],
    relationships: [],
  };
  return { provenance, sbom };
}
