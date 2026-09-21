# Release provenance and providerd succession closure — 2026-09-21

Status: **DEPLOYED AND PRODUCTION-VERIFIED**

This record closes only the release-provenance and build-identity succession
campaign. It does not close the synthetic-user product gaps F2b, F5, or S23 and
does not begin Marginalia V3, FELT, retrieval redesign, provider expansion, or
UI redesign.

## Canonical compatibility set

| Component | Canonical identity |
| --- | --- |
| Marginalia source | `380037cb432e2195ffe89e4df33c4558d6c0c6e6` |
| AG-ng source | `a73c6a1653a8f056a6798ad36fd1c4dc23430968` |
| Docket source | `181589f910b76030b312d6478bd0ac813a630855` |
| OCI image | `sha256:bfde456d03c4f26b8289c21cfdd6a08392b9ce1b6454ab0aabd154602b4d2ca5` |
| Local image reference | `marginalia:provenance-succession-380037c-candidate` |
| Provider activation build identity | `sha256:5d21e5a5456f208423496a72a3267833bcd101b91337da2b528559900058be9d` |

All five production services (`marginalia`, `marginalia-generation`,
`marginalia-providerd`, `marginalia-backup`, and `marginalia-synthetic`) resolve
to the OCI image above. The deployed image labels name the three exact source
revisions. A second build from the clean context and the recorded build
arguments reproduced the same image ID.

The active deployment adds the image/provenance-only override
`/opt/marginalia-local/compose.release-provenance-20260921.yaml` to the existing
base and AG-ng overlay. It also sets the non-secret deployment identity to
`marginalia-release-provenance-20260921` for web and backup manifests. Existing
secret/configuration files were neither read into this record nor changed.

## Source recovery

The Sep-17 dirty-derived product changes were separated from pre-existing
author work into reviewable commits:

1. `dcd0454` — exact product-source recovery;
2. `a012640` — recovered qualification and Playwright witnesses;
3. `f39ff26` — reliability/API/backlog documentation, including explicit
   successor gaps;
4. `380037c` — exact AG-ng succession companion pin and OCI labels.

The five recovered product files in the clean commit match the corresponding
files in the running image. For example, `adapter.py` is
`sha256:028b014a00e0ceb8b55f6579c53dc8f5d8339e3972ac1e56377b2af127de0be3`
in both Git and the image.

The original dirty worktree was not reset, cleaned, overwritten, or committed.
Its untracked Sep-17 qualification file was recovered deliberately, retained in
place, and also committed on the isolated campaign branch. The original file's
pre-campaign SHA-256 is
`25608f350b1827d7b329365ab8c0e9109134f68ca354501cf4f195032116b072`.
The committed successor-aware test file is
`sha256:b0356c7882677402bb6f68b074c0690246599214554aa8f1738e31693f1c393b`.

Private recovery evidence is retained under
`/data/git/agent_gov_ui/.campaign-artifacts/marginalia-release-provenance-20260921/`, including the
original staged/unstaged patches, untracked-file archive, all-refs bundle,
operator authorization, qualification and production plans, provider-store
recovery archives, and their checksums. It contains no disclosed credential
values.

## Supported build succession

AG-ng commit `a73c6a1` supplies the smallest build-only offline succession
contract. It preserves activation checks rather than bypassing them:

- candidate identity is derived from the running executable and exact config;
- preflight opens SQLite read-only and proves custody, integrity, chain,
  activation, head, lineage revision, backup-barrier absence, and exact plan;
- commit re-proves those facts in one immediate transaction, appends a lineage
  event, and compare-and-swaps only the activation record;
- only build identity may change; authority, epoch, config, security profile,
  and catalog drift are refused;
- reversal may target only the immediately recorded predecessor and appends a
  new receipt; stale plans fail closed.

The isolated production-store snapshot passed forward and reverse succession.
Pre-existing event-prefix, non-succession materialized-state, identity, blob,
and backup-table digests were identical before and after. The complete AG store
suite passed: 31 unit tests and 8 integration tests.

Production forward succession committed at chain sequence `1380`, digest
`sha256:7369a2a79740f3529f3ab8c5978e0b488e8c6ab3bd1268ff2aa8650970f16a73`,
lineage revision `1`. The exact production rollback plan was then executed only
against a cloned post-transition volume. It restored the immediately recorded
predecessor at clone sequence `1381` while preserving the pre-transition event
prefix and all non-succession materialized state. Live custody remains on the
canonical successor.

## Qualification and recovery evidence

- Focused product/storage qualification: **88 passed, 1 skipped, 2 xfailed**.
- Browser reliability qualification: **8 passed**.
- F2b and S23 are strict expected failures; F5 is an explicit skip pending its
  product decision. These results preserve, rather than resolve, the gaps.
- Clean-context rebuild reproduced OCI image
  `sha256:bfde456d03c4f26b8289c21cfdd6a08392b9ce1b6454ab0aabd154602b4d2ca5`.
- Pre-transition recovery backup:
  `marginalia-erin-20260921T214707107752Z.zip`, SHA-256
  `5008effb7c35650369f333842642aa77b841bb9d5791995256b23e2e54db1804`.
- The archive passed outer/member verification, the built-in isolated restore
  rehearsal, and a separate restore into the blank volume
  `marginalia_restore_provenance_20260921`.
- Canonical post-deploy backup:
  `marginalia-erin-20260921T221834415821Z.zip`, SHA-256
  `51db5de260c9401f8bed95a815fabaff012c3aaa2a4be53f32839598410efe93`.
  The canonical backup service created and verified it with deployment identity
  `marginalia-release-provenance-20260921`, then repeated the isolated
  restore-test successfully.
- The stopped pre-transition providerd volume is retained as an opaque recovery
  archive with SHA-256
  `3bfdb6eddaa6467581818f16658d25376e8cfd7d6c08d5b7b076d88e07a8a9db`.
- Retained predecessor images and the exact append-only reversal plan provide
  rollback materials. Restoring the old provider database over a successful
  transition is not the normal rollback path.

No discretionary provider/model request was made. Existing scheduled synthetic
behavior was stopped during maintenance and resumed under its established
bounded policy afterward.

## Final live checks

After maintenance was lifted:

- `/health/live`: alive;
- `/health/ready`: ready;
- runtime: healthy, AG-ng authoritative, Docket custody true, provider socket
  ready, model catalog valid, classic fallback false;
- `provider_readiness`: fresh (the predecessor protocol had reported absent);
- backup destination: writable remote NFS;
- maintenance: inactive;
- deployment identity: `marginalia-release-provenance-20260921`;
- all five containers: running on the same canonical image digest;
- provider selection: the configured default and every availability decision
  are projected without a smoke call;
- the established synthetic worker is running on the canonical image.

One derived-context summary remains required with zero active maintenance tasks.
That is preserved resumable context work, not an unsafe active provider
operation and not a release-provenance defect. Five older requests remain in
their pre-existing `reconciliation_required` state; the campaign did not erase,
settle, retry, or reinterpret them.

## Explicit successor work

The following remain open and must not be described as fixed by this release:

- **F2b:** multi-writer working-copy CAS and a recoverable conflict experience;
- **F5:** product semantics for model-driven revision of an existing artifact;
- **S23:** restoration of a prompt draft created before a conversation exists.

Their tests remain in `tests/test_qualification_repairs_20260917.py`. The next
synthetic-user closure campaign must treat the xfail/skip states as declared
expectations, not silently relax or delete them.
