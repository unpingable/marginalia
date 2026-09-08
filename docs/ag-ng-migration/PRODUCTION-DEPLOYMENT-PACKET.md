# Full ag-ng migration — production deployment packet

Candidate: `8d672105d0c179120ba516f9df7254f0fa2a2cbd`  
Qualified local image ID:
`sha256:42da34573d510ff1e00572aff4026486cbbf2b0a87adc0b82aac2121c7ab26b4`

Production has **not** been changed by this campaign. Do not deploy until the
owner gives a separate production decision.

## Mandatory preconditions

1. Revoke and rotate the OpenRouter credential that was exposed during the
   earlier production inspection. Do not reuse it. Put the replacement only in
   `/tank/nfs/marginalia/ag-ng/production/secrets/providerd/openrouter-api-key`
   with mode `0600`; remove plaintext provider credentials from container
   environment configuration.
2. Create the production catalog, provider identities, issuer key, evidence
   keyring, and matching provider policy under
   `/tank/nfs/marginalia/ag-ng/production/` as documented in
   `docs/MODEL_PROVIDERS.md`. Copy the recovery set to a separately protected NAS
   recovery directory and prove a restored ciphertext sample decrypts with the
   separately supplied key.
3. Freeze writes, stop new dispatches, reconcile all existing durable work, and
   take a fresh verified backup. Restore it in isolation. Record the final
   session/message/canon/artifact census and confirm there is no unresolved
   classic pending item.
4. Use this exact candidate image, or publish this already-qualified image and
   pin its registry digest. A rebuild is a new candidate and must repeat affected
   validation. No production image may be built from uncommitted source.
5. Verify production Compose resolves all five services to the same candidate
   digest and uses only file-mounted NAS credentials. Keep the web and worker
   away from provider API credentials.

## Cutover and acceptance

Deploy the same qualified digest to `marginalia`, `marginalia-generation`,
`marginalia-providerd`, `marginalia-backup`, and `marginalia-synthetic`. Then:

- verify `/health/ready` names ag-ng as authoritative, the provider socket is
  ready, Docket custody is enabled, and classic fallback is false;
- confirm the header **Generation enabled/paused** control opens Generation reliability and
  accurately changes only new-dispatch admission;
- run one real writer generation and verify exact model/route, reported usage,
  and known/estimated/unavailable cost in the UI;
- exercise lost acknowledgement and exact replay without another provider
  dispatch;
- verify unknown work remains inspectable and is never silently retried;
- verify backup creation and a separately keyed restore rehearsal;
- compare the post-cutover census with the frozen pre-cutover census.

Record the deployed registry digest, deployment ID, acceptance time, backup ID,
recovery-key version, and observer. Any repair invalidates the affected
validation and creates a new candidate.

## Rollback boundary

The previous image passed read/write qualification against a copy of the
migrated additive state. Before rollback, stop new dispatches and reconcile or
record every pending ag-ng dispatch; the previous image cannot assume custody of
new provider executions. Restore the pre-cutover backup if the failure affects
state semantics. Retain the ag-ng generation database and encrypted evidence as
an opaque incident record even if the old image is serving traffic.

Rollback does not restore or unbill a provider execution, and confirmed local
cancellation does not prove the provider never executed it.
