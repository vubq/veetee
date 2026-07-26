---
name: safe-refactor
description: Refactor Veetee code incrementally while preserving public behavior and protocol compatibility. Use for multi-file structural cleanup in firmware, voice-server, Manager API/Web, contracts or provider SDK.
---

# Safe Refactor

1. Inspect `git status`; preserve unrelated work.
2. Identify the owning subsystem and read its task-specific docs and closest tests.
3. State a short plan, observable behavior to preserve and compatibility constraints.
4. Refactor one coherent group at a time. Reuse existing utilities before introducing abstractions.
5. After each group, run the narrowest relevant typecheck, lint or test.
6. Broaden validation only after the focused checks pass.
7. Review the final diff for accidental API, schema, protocol, generated-file and formatting changes.

Do not edit `references/`, perform unrelated cleanup, redesign public APIs, add hypothetical compatibility shims or hand-edit generated files. Public API, database, NVS, provider configuration and wire changes require explicit version/migration work and are outside a pure refactor.

Do not commit, push, deploy, publish, release or flash. Report behavior preserved, files changed, tests run and residual hardware/runtime gaps.
