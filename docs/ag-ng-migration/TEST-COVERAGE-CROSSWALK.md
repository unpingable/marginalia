# ag-ng migration test-coverage crosswalk

This crosswalk compares the last pre-migration Gate 3H Marginalia candidate,
`109a0ef8c612f42f83be5376f6f585350e8ea796`, with the first full ag-ng
candidate, `8d672105d0c179120ba516f9df7254f0fa2a2cbd`. It distinguishes removed
products and contracts from writer behavior and migration invariants that must
remain qualified.

## Exact collection arithmetic

The Gate 3H suite collected and executed 839 tests. The first ag-ng candidate
collected 449:

- 416 test node IDs were unchanged;
- 423 Gate 3H node IDs were absent;
- 33 ag-ng node IDs were new;
- therefore `839 - 423 + 33 = 449` collected;
- 448 executed and passed in the ordinary host run;
- one exact-companion witness was skipped because its two pinned executable
  paths were not supplied to that particular pytest process.

The skipped node was
`test_generation_companion_processes.py::test_exact_companions_dispatch_once_and_reopen_settled_state`.
It requires `MARGINALIA_TEST_AG_LOOPCTL` and `MARGINALIA_TEST_DOCKET`. The
release workflow now extracts those exact binaries from the built candidate
image and runs the witness explicitly. A release qualification must report the
ordinary collection and the exact-companion result separately; a skipped
witness is not a passing companion qualification.

Of the 423 absent Gate 3H IDs, 418 came from ten retired modules. Their count is
an executable inventory in `tests/conftest.py` and is checked by
`test_retired_test_inventory_has_an_audited_crosswalk`.

That inventory guard was one additional test in the first corrected candidate.
The later semantic review did not treat that count as acceptance. It removed
one misleading parameter that asserted the historical archive was a donor 404
and added eleven product-boundary tests: configured authentication, session
defaults and missing resources, synthetic isolation, artifact error mapping,
and seven historical-receipt access/integrity cases. The resulting suite
collects 460. An ordinary host run can execute 459 and conditionally skip only
the exact-companion witness; release qualification must supply the candidate's
two companion binaries and execute all 460.

| Retired module | Gate 3H cases | Disposition |
| --- | ---: | --- |
| `test_adapter.py` | 248 | Mixed classic-daemon, donor-product, and old combined-adapter tests. Its fixture explicitly selects `GOVERNOR_MODE=general` and disables the ag-ng-only boundary, so it cannot qualify the shipped fiction product. Its still-applicable assertions were reviewed and ported by behavior, as mapped below. |
| `test_code_builder_smoke.py` | 5 | Code-builder donor product removed from Marginalia. |
| `test_dashboard_v2_api.py` | 39 | Agent Governor operator dashboard and run API removed from the writer product. |
| `test_governed_chat_adapter.py` | 6 | Classic daemon receipt/stream adapter replaced by durable request, dispatch, evidence, and acceptance tests. |
| `test_intent_api.py` | 25 | General/code/research intent compiler donor API removed from Marginalia. |
| `test_live_governed_chat_contract.py` | 1 | Classic live-daemon contract replaced by the exact ag-ng/Docket witness and crash/reconciliation qualification. |
| `test_parity.py` | 10 | Classic daemon parity and daemon-auth translation contract replaced by ag-ng provider and boundary tests. |
| `test_reliability.py` | 10 | Classic RPC/socket supervisor tests replaced by executor/worker reconciliation tests and isolated crash tests. |
| `test_research_builder_smoke.py` | 12 | Research-builder donor product removed from Marginalia. |
| `test_summaries.py` | 62 | Classic Governor operator status/why/history summaries removed with that dashboard; these were not manuscript context summaries. |

The remaining five removed IDs were direct contract replacements:

- three single-container installer/launcher tests became four fail-closed
  launcher and multi-service Compose isolation tests in `test_distribution.py`;
- the relative classic provider work-directory test became the closed,
  non-secret command-environment and provider configuration tests in
  `test_ag_provider_config.py`;
- the classic `/governor/receipts/export` route-presence check was superseded
  by the executable export and integrity cases in
  `test_historical_receipts.py`.

## Semantic disposition of the mixed adapter module

The 248-case module was split semantically rather than copied wholesale. The
table names every behavior family it contained. “Retired” means the behavior
belonged to a removed donor product or Classic operator contract; it does not
mean a writer invariant was left untested.

| Baseline behavior family | Disposition and active executable replacement | Lost negative-case coverage |
| --- | --- | --- |
| Writing shell, product identity, and API discovery | Preserved by `test_root_is_an_intentional_marginalia_writing_shell`, `test_product_api_info_lists_only_writing_surfaces`, and `test_information_architecture.py`. | Donor terms and routes are explicitly absent; no gap. |
| Health, liveness/readiness, models, backend discovery, and selection | Preserved by `test_ag_ng_health_names_the_real_execution_owners`, provider API tests, model-provider configuration tests, and distribution preflight. Classic daemon fields and environment-selected backends were retired. | Unknown models, missing credentials, model substitution, unavailable providers, and non-switchable AG ownership all have active refusal cases; no gap. |
| Chat response shape, transactional generation, cancellation/failure, streaming finality, constraints, footer, and receipt semantics | Replaced by `test_generation_executor.py`, `test_generation_worker.py`, `test_generation_acceptance.py`, `test_generation_boundaries.py`, `test_provider_api.py`, and the exact ag-ng/Docket witness. The Classic footer and `receipt_v1` authorization claim were retired; candidate/evidence identity is the replacement. | Active cases cover blank/malformed provider results, cancellation, indeterminate custody, no partial prose, stale revisions, changed canon/guidance, duplicate delivery, and safe errors; no gap. |
| Session list/create/get/update/delete/append and round trips | Preserved by `test_session_store.py`, `test_library_store.py`, and conversation lifecycle tests in `test_information_architecture.py`. | The baseline's default-title and missing GET/PATCH/DELETE/append cases were absent from the active HTTP boundary; `test_session_api_preserves_defaults_and_missing_resource_errors` ports them. |
| Fiction capture, review, characters, world rules, and restrictions | Preserved by `test_canon_review_store.py`, `test_canon_authority.py`, story/capture integration in `test_information_architecture.py`, and promotion/refusal cases in `test_marginalia_product.py`. | Missing/resolved candidates, invalid authority, unresolved referents, and uncanonical premises remain executable; no gap. |
| Artifact CRUD, provenance, revision history, style policy, and optimistic concurrency | Preserved by `test_artifact_store.py` and artifact lifecycle integration in `test_information_architecture.py`. Research/code style branches were retired with those donor modes. | Store-level invalid-kind, missing-content, missing-version, and stale-write cases remained. `test_artifact_api_preserves_validation_not_found_and_stale_conflicts` restores their HTTP status/error-shape boundary. |
| Historical `receipt_v1` export and verification | Preserved as a read-only migration archive, never as ag-ng authority. | This was a concrete gap: all three `/v1/historical-receipts/*` handlers were behind the product-route deny boundary and the prior “replacement” only asserted a 404. `test_historical_receipts.py` now executes discovery, canonical export, intact verification, tamper detection, chain-break detection, empty archive, and malformed-line reporting. |
| Optional bearer authentication for writer mutations | Preserved at the fiction-product middleware boundary. | The only positive/negative cases were in the excluded mixed module. `test_configured_writer_auth_protects_mutations_without_closing_reads` ports open reads plus missing, wrong, correct, and DELETE-token cases. |
| Synthetic ag-ng health generation isolation | Preserved by the internal-generation custody path and `test_durable_internal_generation.py`. | The durable tests proved result custody but did not assert that the HTTP synthetic probe leaves a real writer session byte-for-byte unchanged. `test_internal_synthetic_generation_cannot_mutate_writer_sessions` ports that negative invariant and checks the separate session store. |
| Classic Governor status/now/why/history/detail UI and effective-config summaries | Retired Classic operator product. These are also the behaviors in excluded `test_summaries.py`; they are not manuscript context summaries. | Not applicable to the writer product. Context summaries have independent admission, evidence-coverage, staleness, and failure tests. |
| Research ledger, research capture, why-overlay, and research style/config | Retired research donor product. | Not applicable; no research routes or UI are advertised. |
| Code project/plan/files/run, constraint injection, and code style/config | Retired code-builder donor product. | Not applicable; no code routes or UI are advertised. |
| Generic `/governor/export` and `/governor/import` | Split. Writer export is preserved by project JSON/ZIP export tests. Legacy state migration is covered by state-layout and library migrations. The generic anchor-import API was never a Marginalia writing-room workflow and was retired rather than relabeled as a project importer. | Writer export, migration, duplicate-state refusal, and restore have negative coverage. A new authoring import workflow would be new architecture, not a missing ag-ng migration assertion. |

## Semantic disposition of the other excluded modules

| Excluded module | Still-applicable behavior and executable replacement | Negative-case conclusion |
| --- | --- | --- |
| `test_code_builder_smoke.py` | None; complete-loop, phase, file-run, and transition behavior belonged to the removed code donor. | Its stale, invalid-transition, and run-failure cases are not writer contracts. Artifact/session concurrency has its own active negatives. |
| `test_dashboard_v2_api.py` | None; run lists, controls, demos, claims, reports, and dashboard HTML belonged to the Classic operator dashboard. | Its missing-run/artifact/demo cases retired with those endpoints. Durable generation inspection is covered through `/v1/generations/*`. |
| `test_governed_chat_adapter.py` | Receipt authority, provider discovery, context binding, and withheld streaming are replaced by ag-ng authorization, the frozen request digest, Docket custody, evidence storage, and candidate acceptance. | Missing/unconfirmed authorization, route substitution, and pre-finality leakage are covered by provider-gateway, generation-boundary, and exact-witness cases. |
| `test_intent_api.py` | None; general/code/research intent templates and compilation were a donor API. | Invalid template/schema/value cases retired with the unadvertised endpoints. |
| `test_live_governed_chat_contract.py` | The applicable block/restart/pending-isolation/resolve-authority sequence is replaced by durable conflict state, exact custody, and revision-checked acceptance. | Cross-project isolation, stale acceptance, unresolved custody, and recovery without redispatch execute in active tests and the cross-process witness. |
| `test_parity.py` | Applicable auth/dispatch/footer concerns moved from Classic-daemon translation to ag-ng/providerd plus Marginalia acceptance. | Missing credentials, transport/auth normalization without secret leakage, provider substitution, and operational-metadata separation all remain active. |
| `test_reliability.py` | Deadline, cancellation, child-process cleanup, readiness, and synthetic-probe concerns remain applicable but the Classic RPC/socket supervisor does not. They execute in local-command, model-provider, worker/executor, usage-accounting, health, and crash tests. | Active negatives cover total deadlines, nonzero exits, cancellation, unknown custody, stopped workers, and no redispatch. |
| `test_research_builder_smoke.py` | None; research drafts, extensions, validator, phases, and bans belonged to the removed research donor. | Its validator and transition failures are not fiction-writer contracts. |
| `test_summaries.py` | None directly; it derived Classic dashboard pills, referee voice, why feed, and history. | Manuscript context summarization is separately covered for malformed output, insufficient evidence, stale source, overflow, failed maintenance, and admission refusal. |

The semantic review found and repaired four concrete preservation gaps: archive
routability/integrity, configured-auth negatives, session-boundary negatives,
and synthetic-session isolation. It also restored artifact HTTP error mapping
that had survived only at the store layer. No still-applicable assertion remains
known only to an excluded module.

## Writer workflow preservation

| Writer-visible workflow | Active ag-ng-only evidence |
| --- | --- |
| Writing room loads and exposes only writing surfaces | `test_root_is_an_intentional_marginalia_writing_shell`; `test_product_api_info_lists_only_writing_surfaces`; `test_information_architecture.py`; browser startup check |
| Projects and conversations create, persist, reload, archive, and remain isolated | `test_library_store.py`; `test_library_store_concurrency.py`; `test_session_store.py`; `test_project_b_cannot_receive_project_a_prompt_context` |
| Project brief, collaborator stance, and voice reach generation | `test_project_settings_persist_and_reach_every_governed_fiction_request` |
| Writer prompt is idempotent and never dispatched synchronously | `test_durable_chat_is_idempotent_and_does_not_dispatch_synchronously`; `test_ag_ng_only_mode_never_falls_back_to_synchronous_generation` |
| Reload/lost acknowledgement recovers the accepted result | `test_lost_ack_replay_returns_historical_acceptance_even_after_switch_off`; Playwright reload witness |
| Rapid double-submit and two tabs cannot insert twice | Playwright rapid-submit witness; `test_two_tabs_from_one_revision_accept_exactly_one_durable_candidate`; session revision CAS tests |
| Provider failure, unknown custody, and fetched response never leak partial or duplicate prose | `test_generation_executor.py`; `test_generation_worker.py`; `test_generation_store.py`; exact-companion witness |
| Acceptance remains revision-, canon-, guidance-, and candidate-bound | `test_generation_acceptance.py`; `test_generation_boundaries.py`; `test_canon_authority.py` |
| Canon changes are author-controlled; model classifications remain proposals | `test_canon_review_store.py`; `test_conflict_resolution_proposes_canon_without_accepting_response`; canon promotion/refusal cases in `test_marginalia_product.py` |
| Characters, world rules, restrictions, drafts, and manuscript structure persist | `test_creative_project.py`; `test_artifact_store.py`; `test_manuscript_store.py`; fiction/canon cases in `test_marginalia_product.py` |
| Writer export contains guidance, story bible, conversations, and draft revisions | `test_writer_export_contains_project_bible_conversations_and_drafts` |
| Context maintenance cannot silently discard story facts or admit stale summaries | `test_context_maintenance.py`; context admission and maintenance cases in `test_marginalia_product.py` |
| Model provenance, token usage, and honest cost state survive persistence | `test_session_store.py`; `test_usage_accounting.py`; Playwright usage/cost witness |
| Generation control is discoverable and pauses only new dispatches | `test_durable_generation_toggle_is_prominent_and_guarded`; `test_kill_switch_stops_new_dispatch_but_preserves_inspection`; Playwright enabled/paused checks |

## Migration and release invariants

| Invariant | Evidence |
| --- | --- |
| Classic is not installed or selectable | `test_distribution.py`; image distribution smoke; ag-ng-only backend and health tests |
| Every service uses the same image while credentials remain process-isolated | Compose distribution tests and all four rendered-overlay checks |
| Legacy state becomes write-through state, not an independent fork | `test_state_layout.py`; previous-image read/write/candidate-read qualification |
| Two independent state trees fail closed | `test_migration_refuses_two_independent_state_trees` |
| Backups restore, ciphertext needs the separate key, and NAS-required mode does not fall back locally | `test_backup_store.py`; isolated evidence restore |
| Exact ag-ng authorization and Docket custody compose across processes | exact-companion witness plus isolated provider/worker crash qualification |
| A replacement worker recovers custody, not provider execution | worker/executor no-redispatch tests and killed-worker provider-count witness |
| A stale response cannot enter the story, while a prior accepted insertion remains idempotent | generation acceptance tests and isolated concurrent-edit qualification |

This table is a release artifact, not a claim that test counts are equivalent.
Retiring another test module, changing its baseline count, or removing a mapped
writer invariant must update both the executable inventory and this crosswalk.
