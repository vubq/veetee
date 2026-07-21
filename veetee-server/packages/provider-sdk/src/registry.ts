import type { AnyProvider, ProviderKind } from "./providers.js";

export class ProviderRegistry {
  private readonly providers = new Map<string, AnyProvider>();

  public register(provider: AnyProvider): void {
    const key = this.key(provider.capabilities.kind, provider.capabilities.providerId);
    if (this.providers.has(key)) {
      throw new Error(`Provider already registered: ${key}`);
    }
    this.providers.set(key, provider);
  }

  public resolve<TProvider extends AnyProvider>(kind: ProviderKind, providerId: string): TProvider {
    const key = this.key(kind, providerId);
    const provider = this.providers.get(key);
    if (!provider) {
      throw new Error(`Provider not registered: ${key}`);
    }
    return provider as TProvider;
  }

  public capabilities(): readonly AnyProvider["capabilities"][] {
    return [...this.providers.values()].map((provider) => provider.capabilities);
  }

  private key(kind: ProviderKind, providerId: string): string {
    return `${kind}:${providerId}`;
  }
}
