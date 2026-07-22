import { createHash } from "node:crypto";
import { open, readdir, readFile, stat } from "node:fs/promises";
import { basename, join, relative, resolve, sep } from "node:path";

export const UI_PACK_MAGIC = Buffer.from("VTPACK1\0", "ascii");
export const UI_PACK_FORMAT_VERSION = 1;
export const UI_PACK_ABI = 1;
export const UI_PACK_HEADER_BYTES = 64;
export const UI_PACK_ENTRY_BYTES = 128;
export const UI_PACK_MAX_ENTRIES = 32;
export const UI_PACK_MAX_BYTES = 2 * 1024 * 1024;

const memberKinds = new Map([
  ["manifest.json", 1],
  ["theme.json", 2],
]);
const safeName = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,62}$/;
const safeId = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const semver = /^\d+\.\d+\.\d+$/;
const executableExtension = /\.(?:js|mjs|cjs|wasm|elf|exe|dll|so|dylib|bin\.exe)$/i;
const stateNames = [
  "starting",
  "wifi_configuring",
  "network_connecting",
  "activating",
  "pairing_recovery",
  "idle",
  "connecting",
  "listening",
  "evaluating",
  "thinking",
  "speaking",
  "aborting",
  "closing",
];

function align(value, boundary) {
  return Math.ceil(value / boundary) * boundary;
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function memberKind(name) {
  const fixed = memberKinds.get(name);
  if (fixed) return fixed;
  if (/^strings\/[A-Za-z0-9-]+\.json$/.test(name)) return 3;
  if (/^fonts\/[A-Za-z0-9._-]+\.vfont$/.test(name)) return 4;
  if (/^icons\/[A-Za-z0-9._-]+\.vicon$/.test(name)) return 5;
  if (/^backgrounds\/[A-Za-z0-9._-]+\.rgb565$/.test(name)) return 6;
  if (/^sounds\/[A-Za-z0-9._-]+\.opus$/.test(name)) return 7;
  throw new Error(`UI Pack member is not allowed: ${name}`);
}

function validateName(name) {
  if (
    !safeName.test(name) ||
    name.startsWith("/") ||
    name.endsWith("/") ||
    name.includes("\\") ||
    executableExtension.test(name) ||
    name.split("/").some((part) => part === "." || part === ".." || !part)
  ) {
    throw new Error(`Unsafe UI Pack member name: ${name}`);
  }
}

function parseJson(buffer, name) {
  if (buffer.length === 0 || buffer.length > 64 * 1024 || buffer.includes(0)) {
    throw new Error(`${name} is empty, too large or contains NUL`);
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(buffer));
  } catch {
    throw new Error(`${name} is not valid UTF-8 JSON`);
  }
}

function object(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} must be an object`);
  }
  return value;
}

function string(value, name, pattern) {
  if (typeof value !== "string" || !value || (pattern && !pattern.test(value))) {
    throw new Error(`${name} is invalid`);
  }
  return value;
}

function integer(value, name, minimum, maximum) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} is invalid`);
  }
  return value;
}

function validateManifest(value) {
  const manifest = object(value, "manifest.json");
  integer(manifest.schema_version, "manifest.schema_version", 1, 1);
  if (manifest.kind !== "ui_pack") throw new Error("manifest.kind must be ui_pack");
  string(manifest.id, "manifest.id", safeId);
  string(manifest.version, "manifest.version", semver);
  string(manifest.theme_id, "manifest.theme_id", safeId);
  if (!["development", "canary", "stable"].includes(manifest.channel)) {
    throw new Error("manifest.channel is invalid");
  }
  string(manifest.license, "manifest.license");
  const target = object(manifest.target, "manifest.target");
  if (
    target.board !== "veetee-s3-n16r8" ||
    target.display !== "st7789-240x320-rgb565"
  ) {
    throw new Error("UI Pack target is incompatible");
  }
  const compatibility = object(manifest.compatibility, "manifest.compatibility");
  integer(compatibility.resource_abi, "manifest.compatibility.resource_abi", 2, 2);
  integer(compatibility.ui_abi, "manifest.compatibility.ui_abi", UI_PACK_ABI, UI_PACK_ABI);
  string(compatibility.min_firmware, "manifest.compatibility.min_firmware", semver);
  string(
    compatibility.max_firmware_exclusive,
    "manifest.compatibility.max_firmware_exclusive",
    semver,
  );
  if (!Array.isArray(manifest.locales) || manifest.locales.length === 0) {
    throw new Error("manifest.locales must not be empty");
  }
  for (const locale of manifest.locales) string(locale, "manifest.locale", /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})+$/);
  string(manifest.fallback_theme_id, "manifest.fallback_theme_id", safeId);
  return manifest;
}

function validateColor(value, name) {
  string(value, name, /^#[0-9a-fA-F]{6}$/);
}

function validateTheme(value, expectedThemeId) {
  const theme = object(value, "theme.json");
  integer(theme.schema_version, "theme.schema_version", 1, 1);
  integer(theme.ui_abi, "theme.ui_abi", UI_PACK_ABI, UI_PACK_ABI);
  if (theme.theme_id !== expectedThemeId) throw new Error("theme_id does not match manifest");
  if (!["signal", "monolith", "quiet"].includes(theme.composition)) {
    throw new Error("theme.composition is unsupported");
  }
  const palette = object(theme.palette, "theme.palette");
  for (const state of stateNames) {
    const colors = object(palette[state], `theme.palette.${state}`);
    validateColor(colors.background, `theme.palette.${state}.background`);
    validateColor(colors.foreground, `theme.palette.${state}.foreground`);
    validateColor(colors.accent, `theme.palette.${state}.accent`);
  }
  return theme;
}

function validateStrings(value, locale) {
  const strings = object(value, `strings/${locale}.json`);
  integer(strings.schema_version, `strings/${locale}.schema_version`, 1, 1);
  if (strings.locale !== locale) throw new Error(`strings/${locale}.locale is invalid`);
  const states = object(strings.states, `strings/${locale}.states`);
  for (const state of stateNames) {
    const copy = states[state];
    const entry = object(copy, `strings/${locale}.states.${state}`);
    for (const key of ["kicker", "title", "hint"]) {
      const text =
        key === "hint" && entry[key] === ""
          ? ""
          : string(entry[key], `strings/${locale}.states.${state}.${key}`);
      if ([...text].length > (key === "hint" ? 96 : 40)) {
        throw new Error(`UI string is too long: ${locale}/${state}/${key}`);
      }
    }
  }
  for (const state of Object.keys(states)) {
    if (!stateNames.includes(state)) throw new Error(`Invalid state key: ${state}`);
  }
  return strings;
}

function parseHeader(header, fileSize) {
  if (header.length !== UI_PACK_HEADER_BYTES || !header.subarray(0, 8).equals(UI_PACK_MAGIC)) {
    throw new Error("UI Pack magic is invalid");
  }
  const formatVersion = header.readUInt16LE(8);
  const headerBytes = header.readUInt16LE(10);
  const uiAbi = header.readUInt16LE(12);
  const entryCount = header.readUInt16LE(14);
  const indexOffset = header.readUInt32LE(16);
  const indexBytes = header.readUInt32LE(20);
  const payloadOffset = header.readUInt32LE(24);
  const totalBytes = header.readUInt32LE(28);
  const indexCrc32 = header.readUInt32LE(32);
  const flags = header.readUInt32LE(36);
  if (
    formatVersion !== UI_PACK_FORMAT_VERSION ||
    headerBytes !== UI_PACK_HEADER_BYTES ||
    uiAbi !== UI_PACK_ABI ||
    entryCount < 3 ||
    entryCount > UI_PACK_MAX_ENTRIES ||
    indexOffset !== UI_PACK_HEADER_BYTES ||
    indexBytes !== entryCount * UI_PACK_ENTRY_BYTES ||
    payloadOffset !== align(indexOffset + indexBytes, 16) ||
    totalBytes !== fileSize ||
    totalBytes > UI_PACK_MAX_BYTES ||
    flags !== 0 ||
    header.subarray(40).some((byte) => byte !== 0)
  ) {
    throw new Error("UI Pack header is invalid");
  }
  return { entryCount, indexOffset, indexBytes, payloadOffset, totalBytes, indexCrc32 };
}

function parseIndex(index, header) {
  if (crc32(index) !== header.indexCrc32) throw new Error("UI Pack index CRC32 mismatch");
  const entries = [];
  const names = new Set();
  let previousEnd = header.payloadOffset;
  for (let position = 0; position < index.length; position += UI_PACK_ENTRY_BYTES) {
    const entry = index.subarray(position, position + UI_PACK_ENTRY_BYTES);
    const terminator = entry.subarray(0, 64).indexOf(0);
    if (terminator <= 0) throw new Error("UI Pack member name is not terminated");
    const name = entry.subarray(0, terminator).toString("ascii");
    validateName(name);
    if (names.has(name)) throw new Error(`Duplicate UI Pack member: ${name}`);
    names.add(name);
    const kind = entry.readUInt16LE(64);
    const flags = entry.readUInt16LE(66);
    const offset = entry.readUInt32LE(68);
    const bytes = entry.readUInt32LE(72);
    const sha256 = entry.subarray(76, 108).toString("hex");
    const alignment = entry.readUInt32LE(108);
    if (
      kind !== memberKind(name) ||
      flags !== 0 ||
      bytes === 0 ||
      alignment !== 16 ||
      offset % alignment !== 0 ||
      offset < header.payloadOffset ||
      offset < previousEnd ||
      offset + bytes > header.totalBytes ||
      entry.subarray(112).some((byte) => byte !== 0)
    ) {
      throw new Error(`UI Pack member index is invalid: ${name}`);
    }
    previousEnd = offset + bytes;
    entries.push({ name, kind, offset, bytes, sha256, alignment });
  }
  for (const required of ["manifest.json", "theme.json", "strings/vi-VN.json"]) {
    if (!names.has(required)) throw new Error(`UI Pack is missing ${required}`);
  }
  return entries;
}

async function listFiles(root, directory = root) {
  const output = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) throw new Error("UI Pack source must not contain symlinks");
    const absolute = join(directory, entry.name);
    if (entry.isDirectory()) output.push(...(await listFiles(root, absolute)));
    else if (entry.isFile()) output.push(relative(root, absolute).split(sep).join("/"));
    else throw new Error("UI Pack source contains an unsupported file type");
  }
  return output;
}

export async function buildUiPack(sourceDirectory) {
  const root = resolve(sourceDirectory);
  const files = (await listFiles(root)).sort();
  if (files.length > UI_PACK_MAX_ENTRIES) throw new Error("UI Pack contains too many members");
  const members = [];
  for (const name of files) {
    validateName(name);
    const data = await readFile(join(root, name));
    if (data.length === 0) throw new Error(`UI Pack member is empty: ${name}`);
    members.push({ name, kind: memberKind(name), data });
  }
  const manifestMember = members.find((member) => member.name === "manifest.json");
  const themeMember = members.find((member) => member.name === "theme.json");
  if (!manifestMember || !themeMember) throw new Error("UI Pack requires manifest.json and theme.json");
  const manifest = validateManifest(parseJson(manifestMember.data, manifestMember.name));
  const theme = validateTheme(parseJson(themeMember.data, themeMember.name), manifest.theme_id);
  for (const locale of manifest.locales) {
    const member = members.find((candidate) => candidate.name === `strings/${locale}.json`);
    if (!member) throw new Error(`UI Pack is missing strings/${locale}.json`);
    validateStrings(parseJson(member.data, member.name), locale);
  }

  const indexBytes = members.length * UI_PACK_ENTRY_BYTES;
  const payloadOffset = align(UI_PACK_HEADER_BYTES + indexBytes, 16);
  let totalBytes = payloadOffset;
  for (const member of members) {
    totalBytes = align(totalBytes, 16) + member.data.length;
  }
  if (totalBytes > UI_PACK_MAX_BYTES) throw new Error("UI Pack exceeds 2 MiB");

  const output = Buffer.alloc(totalBytes);
  const index = output.subarray(UI_PACK_HEADER_BYTES, UI_PACK_HEADER_BYTES + indexBytes);
  let payloadCursor = payloadOffset;
  members.forEach((member, item) => {
    payloadCursor = align(payloadCursor, 16);
    const entry = index.subarray(item * UI_PACK_ENTRY_BYTES, (item + 1) * UI_PACK_ENTRY_BYTES);
    entry.write(member.name, 0, 63, "ascii");
    entry.writeUInt16LE(member.kind, 64);
    entry.writeUInt16LE(0, 66);
    entry.writeUInt32LE(payloadCursor, 68);
    entry.writeUInt32LE(member.data.length, 72);
    createHash("sha256").update(member.data).digest().copy(entry, 76);
    entry.writeUInt32LE(16, 108);
    member.data.copy(output, payloadCursor);
    payloadCursor += member.data.length;
  });

  UI_PACK_MAGIC.copy(output, 0);
  output.writeUInt16LE(UI_PACK_FORMAT_VERSION, 8);
  output.writeUInt16LE(UI_PACK_HEADER_BYTES, 10);
  output.writeUInt16LE(UI_PACK_ABI, 12);
  output.writeUInt16LE(members.length, 14);
  output.writeUInt32LE(UI_PACK_HEADER_BYTES, 16);
  output.writeUInt32LE(indexBytes, 20);
  output.writeUInt32LE(payloadOffset, 24);
  output.writeUInt32LE(totalBytes, 28);
  output.writeUInt32LE(crc32(index), 32);
  output.writeUInt32LE(0, 36);
  return { buffer: output, manifest, theme, members: members.map(({ name, kind, data }) => ({ name, kind, bytes: data.length })) };
}

export async function inspectUiPackReader(readRange, fileSize) {
  if (!Number.isSafeInteger(fileSize) || fileSize <= 0 || fileSize > UI_PACK_MAX_BYTES) {
    throw new Error("UI Pack size is invalid");
  }
  const header = parseHeader(await readRange(0, UI_PACK_HEADER_BYTES), fileSize);
  const index = await readRange(header.indexOffset, header.indexBytes);
  const entries = parseIndex(index, header);
  const decoded = new Map();
  for (const entry of entries) {
    const hash = createHash("sha256");
    let cursor = 0;
    const chunks = [];
    while (cursor < entry.bytes) {
      const length = Math.min(64 * 1024, entry.bytes - cursor);
      const chunk = await readRange(entry.offset + cursor, length);
      hash.update(chunk);
      if (entry.kind <= 3) chunks.push(chunk);
      cursor += length;
    }
    if (hash.digest("hex") !== entry.sha256) throw new Error(`UI Pack member SHA-256 mismatch: ${entry.name}`);
    if (entry.kind <= 3) decoded.set(entry.name, Buffer.concat(chunks));
  }
  const manifest = validateManifest(parseJson(decoded.get("manifest.json"), "manifest.json"));
  const theme = validateTheme(parseJson(decoded.get("theme.json"), "theme.json"), manifest.theme_id);
  for (const locale of manifest.locales) {
    const name = `strings/${locale}.json`;
    if (!decoded.has(name)) throw new Error(`UI Pack is missing ${name}`);
    validateStrings(parseJson(decoded.get(name), name), locale);
  }
  return { manifest, theme, entries, sizeBytes: fileSize };
}

export async function inspectUiPackBuffer(buffer) {
  if (!Buffer.isBuffer(buffer)) throw new Error("UI Pack must be a Buffer");
  return inspectUiPackReader(async (offset, length) => {
    if (offset < 0 || length <= 0 || offset + length > buffer.length) throw new Error("UI Pack read is out of bounds");
    return buffer.subarray(offset, offset + length);
  }, buffer.length);
}

export async function inspectUiPackFile(path) {
  const absolute = resolve(path);
  const info = await stat(absolute);
  if (!info.isFile()) throw new Error("UI Pack is not a regular file");
  const handle = await open(absolute, "r");
  try {
    return await inspectUiPackReader(async (offset, length) => {
      const buffer = Buffer.alloc(length);
      const { bytesRead } = await handle.read(buffer, 0, length, offset);
      if (bytesRead !== length) throw new Error("UI Pack ended unexpectedly");
      return buffer;
    }, info.size);
  } finally {
    await handle.close();
  }
}

export function suggestedUiPackFileName(manifest) {
  return `${basename(manifest.theme_id)}-${manifest.version}.vtp`;
}
