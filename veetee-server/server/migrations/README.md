# Veetee migrations

Migrations run directly against local PostgreSQL. They are SQL-only and must be applied
in a transaction. The first migration creates control-plane ownership, agent configuration,
device assignment, memory and audit tables. The second migration adds provider and
conversation metadata without storing provider secrets.

Apply locally:

```bash
psql -d veetee -f migrations/001_control_plane.sql
psql -d veetee -f migrations/002_runtime_control_plane.sql
```

Rollback only a local/test database:

```bash
psql -d veetee -f migrations/001_control_plane.down.sql
psql -d veetee -f migrations/002_runtime_control_plane.down.sql
```

The migration stores no provider secret. Provider credentials remain environment/secret
references and are never returned by the control-plane API.

Local control plane smoke test uses `VEETEE_PERSISTENCE_ENABLED=true`,
`VEETEE_DATABASE_DSN=dbname=veetee`, and bootstrap credentials supplied only through the
process environment. The default application keeps persistence disabled for existing local
device tests.
