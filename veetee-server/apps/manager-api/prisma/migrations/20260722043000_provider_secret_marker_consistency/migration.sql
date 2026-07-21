UPDATE "ProviderBinding"
SET "secretConfigured" = ("secretCiphertext" IS NOT NULL)
WHERE "secretConfigured" IS DISTINCT FROM ("secretCiphertext" IS NOT NULL);
