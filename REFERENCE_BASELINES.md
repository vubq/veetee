# Reference baselines

The `references/` directory is intentionally excluded from this repository.
It contains upstream projects used for implementation comparison only.

Baseline captured on 2026-09-07:

| Reference | Upstream | Baseline commit | Branch |
| --- | --- | --- | --- |
| xiaozhi-esp32 | https://github.com/78/xiaozhi-esp32.git | `c7241272f2d5fd140c77542f3cf12d09e717fc2f` | `main` |
| xiaozhi-esp32-server | https://github.com/xinnan-tech/xiaozhi-esp32-server.git | `c478257517b892047db3afaaeeb25e2b1e115931` | `main` |

Local verification on 2026-09-08: both reference worktrees were clean and
checked out exactly at the baseline commits above.

To compare with upstream later, fetch inside the corresponding local reference
repository and compare the baseline commit with the new upstream commit.
