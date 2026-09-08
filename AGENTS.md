# Model execution

Before adding a provider SDK, requesting a provider key, or creating another
model shim, inspect Cartography and the supported `model-execution` Python/Rust
paths. Keep provider invocation, ag-ng authorization, Docket custody, response
normalization, and Marginalia acceptance separate. Never silently substitute a
provider or model and never add Agent Governor classic as a dependency or
fallback. Every transitional exception belongs in `classic-exceptions.json`
with an owner and removal gate.
