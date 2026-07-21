import { PrismaClient } from "@prisma/client";

import { seedControlPlane } from "../src/database/seed.js";

const prisma = new PrismaClient();
const adminEmail = process.env.VEETEE_BOOTSTRAP_ADMIN_EMAIL;
const adminPassword = process.env.VEETEE_BOOTSTRAP_ADMIN_PASSWORD;
if (!adminEmail || !adminPassword || adminPassword.length < 12) {
  throw new Error("Set VEETEE_BOOTSTRAP_ADMIN_EMAIL and a 12+ character password before seeding");
}

try {
  await seedControlPlane(prisma, {
    tenantSlug: process.env.VEETEE_BOOTSTRAP_TENANT_SLUG ?? "veetee-local",
    tenantName: process.env.VEETEE_BOOTSTRAP_TENANT_NAME ?? "Veetee Local",
    adminEmail,
    adminPassword,
    adminName: process.env.VEETEE_BOOTSTRAP_ADMIN_NAME ?? "Veetee Owner",
  });
} finally {
  await prisma.$disconnect();
}
