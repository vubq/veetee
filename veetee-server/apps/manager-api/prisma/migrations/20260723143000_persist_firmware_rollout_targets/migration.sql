ALTER TABLE "FirmwareRollout"
ADD COLUMN "selectedDeviceIds" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
