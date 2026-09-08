# Full ag-ng migration — Gate 0 baseline

Date: 2026-09-07

## Frozen source identities

- Marginalia: `17c2da0a9263d25b5d1f994f39c5a56fafb77660`
- Agent Governor classic contract: `e279a94326a0a13dbe43473846b53e4c3a9b31f2`
- ag-ng starting baseline: `cb85d363e2495a75f78c28fb8ce9b46af1f289c0`
- qualified ag-ng provider-ingress candidate: `c3210f156208b22bf21e7bd1910a84a85b519538`
- Docket starting baseline: `c49ad8d0f26fb2a13b9dbafdde84d7abfe1f867b`
- qualified Docket executor-host candidate: `181589f910b76030b312d6478bd0ac813a630855`

The migration branches are `campaign/full-ag-ng-migration-v1`,
`campaign/marginalia-provider-ingress-v1`, and
`campaign/marginalia-generation-executor-host-v1`. They were created from the
exact objects above. No unrelated ag-ng campaign was merged.

## Existing-candidate custody

Before a migration worktree was created, the active ag-ng refs, the dirty
`campaign/governed-campaign-loop-v1-worker-vm` binary diff, the Docket
executor-host branch, and all Marginalia refs were copied to verified Git
bundles under:

```text
/tank/nfs/marginalia/ag-ng-migration/source-custody/2026-09-07/
```

The directory is mode `0700` and its files are mode `0600`. Cartography's
overlapping implementation lane is paused; this campaign must inspect and
adopt an existing candidate when it fits, never overwrite or independently
duplicate it.

## Production observations, not assumptions

At the Gate 0 observation time, production contained no classic pending item
and no durable-generation request or dispatch row. Production did contain
writer sessions, canon anchors, project state, manuscript state, and one
artifact. A final fenced census is still mandatory immediately before cutover.

No persisted production file contained a `/governor/` URL or `governor.sock`
reference. Four classic `_context.json` files contained `.governor` path
metadata. These observations justify no general compatibility router and do
not waive migration testing.

## Surface dispositions

| Classic surface | Disposition |
| --- | --- |
| daemon, JSON-RPC client, provider backends | replace with ag-ng authorization, Docket custody, and ag-providerd |
| fiction prompt augmentation and continuity checking | rehome as Marginalia writer policy with behavior fixtures |
| pending fix/revise/proceed state | rehome as Marginalia generation-conflict state |
| canon anchors and canon capture classifier | rehome under Marginalia ownership and migrate data |
| artifacts, sessions, manuscript, project guidance, export/import | retain and move to the new Marginalia state root |
| classic receipt_v1 and gate receipts | historical read-only archive; preserve bytes, identities, and provenance |
| classic authority tokens, pending records, and receipts | never import as ag-ng runtime authority |
| code/research builders, instrument dashboard, intent demos | retire after dependency and persisted-data census; archive opaque data if found |
| `/governor/*` URLs and `.governor` runtime paths | replace at cutover; add only evidence-driven narrow readers or redirects |

The writer-workflow preservation boundary includes writing, canon, capture,
conflict resolution, artifacts, export/import, and historical-receipt access.
