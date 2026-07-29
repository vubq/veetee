import type { ComputedRef, InjectionKey } from "vue";

export interface VtFieldContext {
  controlId: string;
  describedBy: ComputedRef<string | undefined>;
  invalid: ComputedRef<boolean>;
}

export const vtFieldContextKey: InjectionKey<VtFieldContext> = Symbol("vt-field");
