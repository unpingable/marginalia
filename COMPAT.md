# Compatibility

Marginalia is commit-qualified, not broadly semver-compatible.

| Dependency/contract | Required value |
|---|---|
| Python | `>=3.11` |
| ag-ng source | `ca1713a277b587929a6138ff6dbd3f07b8fe676d` |
| Docket source | `181589f910b76030b312d6478bd0ac813a630855` |
| `receipt-v1` | vendored `0.1.0`, historical read-only use |
| provider RPC | ag-ng fixed-service provider contract at the pinned commit |
| custody | Docket executor-host contract at the pinned commit |

The OCI image must contain `ag-providerd`, `ag-providerctl`, `ag-loopctl`, and
`docket`, and must not contain the `agent-governor` or `receipt-kernel` Python
distributions. Configuration parsers fail closed, and each process receives
only its own copied, root-owned configuration and credentials.

The legacy single-container launcher and installer fail closed. The supported
runtime is the versioned multi-service Docker Compose topology.
