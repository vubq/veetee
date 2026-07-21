# manager-api

Control plane cho auth/tenant, agent config version, device activation, provider credentials, MCP policy, OTA artifact và audit.

Milestone đầu: login dev, agent CRUD, activation 6 số CSPRNG/TTL/atomic consume, provider catalog/health, internal config snapshot và desired/reported device state.

Artifact milestone: wake profile CRUD, model/assets upload qua object-store URL, manifest hash/signature/ABI validation, resource bundle publish/canary/rollback. Không buffer binary lớn trong API process.
