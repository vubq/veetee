import { Injectable, type OnApplicationBootstrap } from "@nestjs/common";

import { PrismaService } from "./prisma.service.js";
import { seedControlPlane } from "./seed.js";

@Injectable()
export class BootstrapService implements OnApplicationBootstrap {
  constructor(private readonly prisma: PrismaService) {}

  async onApplicationBootstrap(): Promise<void> {
    const adminEmail = process.env.VEETEE_BOOTSTRAP_ADMIN_EMAIL;
    const adminPassword = process.env.VEETEE_BOOTSTRAP_ADMIN_PASSWORD;
    if (!adminEmail && !adminPassword) return;
    if (!adminEmail || !adminPassword || adminPassword.length < 12) {
      throw new Error("Bootstrap admin requires an email and password of at least 12 characters");
    }
    await seedControlPlane(this.prisma, {
      tenantSlug: process.env.VEETEE_BOOTSTRAP_TENANT_SLUG ?? "veetee-local",
      tenantName: process.env.VEETEE_BOOTSTRAP_TENANT_NAME ?? "Veetee Local",
      adminEmail,
      adminPassword,
      adminName: process.env.VEETEE_BOOTSTRAP_ADMIN_NAME ?? "Veetee Owner",
    });
  }
}
