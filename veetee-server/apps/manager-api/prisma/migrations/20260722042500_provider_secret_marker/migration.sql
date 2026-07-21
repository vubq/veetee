UPDATE "ProviderBinding"
SET "secretConfigured" = false
WHERE "secretCiphertext" IS NULL;
