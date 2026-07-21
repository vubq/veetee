import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

import { Injectable, ServiceUnavailableException } from "@nestjs/common";

@Injectable()
export class SecretCryptoService {
  encrypt(value: string): string {
    const iv = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", this.key(), iv);
    const encrypted = Buffer.concat([cipher.update(value, "utf8"), cipher.final()]);
    const tag = cipher.getAuthTag();
    return ["v1", iv.toString("base64url"), tag.toString("base64url"), encrypted.toString("base64url")].join(".");
  }

  decrypt(value: string): string {
    const [version, encodedIv, encodedTag, encodedPayload] = value.split(".");
    if (version !== "v1" || !encodedIv || !encodedTag || !encodedPayload) {
      throw new Error("Unsupported encrypted secret format");
    }
    const decipher = createDecipheriv("aes-256-gcm", this.key(), Buffer.from(encodedIv, "base64url"));
    decipher.setAuthTag(Buffer.from(encodedTag, "base64url"));
    return Buffer.concat([
      decipher.update(Buffer.from(encodedPayload, "base64url")),
      decipher.final(),
    ]).toString("utf8");
  }

  private key(): Buffer {
    const encoded = process.env.VEETEE_MASTER_KEY;
    if (!encoded) throw new ServiceUnavailableException("Provider secret encryption is not configured");
    const key = Buffer.from(encoded, "base64");
    if (key.length !== 32) {
      throw new ServiceUnavailableException("VEETEE_MASTER_KEY must decode to exactly 32 bytes");
    }
    return key;
  }
}
