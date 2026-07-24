-- Edge TTS is retired from the Veetee provider catalog. Immutable historical
-- agent snapshots keep their published metadata, but no live binding may remain.
DELETE FROM "ProviderBinding"
WHERE lower("adapter") IN ('edge-tts', 'edge_tts');
