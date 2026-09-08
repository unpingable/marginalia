# Marginalia / ag-ng execution contract

Marginalia is qualified against these immutable companion revisions:

- ag-ng: `1bd4225a94fa82df066923612f713a93ba93a7bb`;
- Docket: `181589f910b76030b312d6478bd0ac813a630855`;
- the vendored, read-only `receipt-v1` compatibility reader at version `0.1.0`.

`AG_NG_CONTRACT_COMMIT` and `DOCKET_CONTRACT_COMMIT` are the build contract.
`sync-deps.sh` exports those exact Git objects into the image context. It does
not read an Agent Governor classic checkout.

The ownership boundary is intentionally split:

1. Marginalia freezes a logical request, its original selection, authorized
   fallback policy, revision, canon, guidance, and relevant request settings.
2. ag-ng authorizes each exact provider dispatch. Each dispatch has its own
   immutable identity for its actual route, model, and body.
3. Docket owns attempt custody and worker reconciliation.
4. Marginalia's executor owns provider transport and encrypted response
   evidence.
5. Marginalia's application alone decides whether a candidate may enter a
   session or become a derived application artifact.

Provider success is not acceptance. Conversation acceptance is a cross-process,
crash-safe compare-and-swap covering session revision, canon, and project
guidance. Candidate identity makes it idempotent. Historical acceptance is
resolved before current fingerprints are checked.

Agent Governor classic, `receipt-kernel`, and their Python runtime APIs are not
installed in the release image. The remaining classic source and tests are
frozen history and cannot be enabled with an environment variable.
