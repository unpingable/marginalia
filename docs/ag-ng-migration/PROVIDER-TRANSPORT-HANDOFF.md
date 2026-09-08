# Marginalia provider transport repair — shared-lane handoff

## Ownership and commits

Marginalia owns this application-forced companion repair. The overlapping
Cartography model-execution/shared-library lane remained read-only and paused.
No Cartography files or branches were modified.

- ag-ng repair branch: `campaign/marginalia-provider-transports-v1`
- exact ag-ng consumer pin: `ca1713a277b587929a6138ff6dbd3f07b8fe676d`
- Docket pin: `181589f910b76030b312d6478bd0ac813a630855`

The Cartography survey's useful finding was preserved: Marginalia is the
qualified forcing case for the broad provider catalog, while extraction into a
native Python/Rust shared library needs a second consumer and its own campaign.
This service remains application-specific and does not replace that selected
library architecture.

## Reusable interfaces

ag-ng provider policy schema `ag.config.providerd.v2` has three disjoint,
tagged transports:

1. `credentialed_https_api`: exact HTTPS URL plus protected credential
   filename/header/prefix.
2. `local_http`: exact HTTP URL, closed operator origin allowlist, and the
   only admitted redirect policy, `deny`.
3. `command`: fixed executable, working directory, closed environment, and
   one built-in structured adapter (`codex`, `claude-code`, or `kimi-code`).
   Its root-owned `model_argument` is `required` or `omit`; omission is
   admitted only for an enrolled Codex provider-default route and never means
   fallback. Claude and Kimi require the exact capability model argument.

The common provider envelope still binds provider, physical model, method,
protocol digest, root policy digest, exact request custody, budget, caller,
session, and dispatch identity. Timeout or ambiguous command I/O leaves the
already-reserved dispatch indeterminate; replay cannot redispatch it.

Marginalia's generator maps its typed catalog into that policy. Multiple
Erin-facing selections may share one physical provider/model policy; the UI
catalog remains complete while the physical policy is emitted once.

## Adoption guidance

A later shared-library campaign should reuse the tagged transport vocabulary,
credential reader, no-redirect client, structured adapter selection,
process-group cleanup, bounded drain, and reserved/available/acknowledged
custody states. It should not copy Marginalia's prompt normalization,
model-menu grouping, fiction acceptance CAS, or provider-specific output
normalizers into ag-ng or Docket.

Adopters must supply:

- their own root-reviewed provider catalog and exact local-origin enrollment;
- external credential/login custody appropriate to the selected variant;
- application-owned response normalization and acceptance authority;
- a deployment-parity test using the exact shipped daemon and production-shaped
  policy.

## Known limitations

- Local HTTP redirects are denied rather than followed within the allowlist.
- Command adapters return whole bounded completion output; they do not provide
  token streaming.
- Process cleanup cannot prove that external execution or billing did not
  occur, so timeout remains indeterminate.
- ag-ng reserves root-configured worst-case cost; Marginalia separately reports
  provider-reported or estimated usage/cost when available.
- Live qualification is route-specific. An unavailable credential,
  subscription login, executable, endpoint, or model remains unavailable and
  is never replaced with another route.
