-- Device config V1 accepts exact ESP-SR model IDs only. Older desired-state
-- projections stored logical aliases such as `wakenet:...` and `multinet:...`.
-- Do not guess a semantic mapping during migration: remove only the incompatible
-- firmware projection, advance its immutable version, and leave agent/resource,
-- Wi-Fi and device identity state intact. Operators can publish a detector-inventory
-- validated wake profile afterwards.
UPDATE "DeviceDesiredState"
SET
  "state" = "state" - 'wakeProfile',
  "version" = "version" + 1,
  "updatedAt" = CURRENT_TIMESTAMP
WHERE
  "state" ? 'wakeProfile'
  AND "state" -> 'wakeProfile' <> 'null'::jsonb
  AND (
    jsonb_typeof("state" -> 'wakeProfile') IS DISTINCT FROM 'object'
    OR jsonb_typeof("state" #> '{wakeProfile,activation}') IS DISTINCT FROM 'object'
    OR COALESCE("state" #>> '{wakeProfile,activation,detectorId}', '')
      !~ '^wn[A-Za-z0-9._-]{1,62}$'
    OR (
      "state" #> '{wakeProfile,interrupt}' IS NOT NULL
      AND "state" #> '{wakeProfile,interrupt}' <> 'null'::jsonb
      AND (
        jsonb_typeof("state" #> '{wakeProfile,interrupt}') IS DISTINCT FROM 'object'
        OR COALESCE("state" #>> '{wakeProfile,interrupt,detectorId}', '')
          !~ '^wn[A-Za-z0-9._-]{1,62}$'
        OR "state" #>> '{wakeProfile,interrupt,detectorId}' =
          "state" #>> '{wakeProfile,activation,detectorId}'
      )
    )
  );
