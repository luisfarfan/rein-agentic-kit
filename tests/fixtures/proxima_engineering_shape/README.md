# proxima-engineering

Governs nine repositories (proxima-api, proxima-admin, proxima-builder,
proxima-storefront-v2, proxima-intelligence-v2, proxima-pos, proxima-app,
proxima-infra, proxima-runtime) and provisions each one.

CI runs `mise run ci`. See `docs/` for the operator runbook: `mise run setup`
to provision a fresh checkout, `mise run doctor` to check one.

This repository has no language manifest of its own -- it is governance
content plus exactly one real project, `harness/`, the Python tool that does
the provisioning.
