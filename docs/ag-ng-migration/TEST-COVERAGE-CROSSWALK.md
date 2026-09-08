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

That inventory guard is one additional test in the corrected candidate. Its
ordinary host run therefore collects 450, executes 449, and reports the same
one conditional skip. With the exact image binaries supplied, the complete
suite executes all 450 with no skip.

| Retired module | Gate 3H cases | Disposition |
| --- | ---: | --- |
| `test_adapter.py` | 248 | Mixed classic-daemon, donor-product, and old combined-adapter tests. Its fixture explicitly selects `GOVERNOR_MODE=general` and disables the ag-ng-only boundary, so it cannot qualify the shipped fiction product. Writer flows were reauthored against the ag-ng-only application as mapped below. |
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
- the classic `/governor/receipts/export` route check became the historical
  receipt archive route check in `test_marginalia_product.py`.

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
