# SPDX-License-Identifier: Apache-2.0
"""Shared test configuration for the ag-ng-only Marginalia product.

These modules are retained only for archaeology.  The baseline case count and
replacement coverage for every entry are audited in
``docs/ag-ng-migration/TEST-COVERAGE-CROSSWALK.md``.  Adding an entry here must
update that crosswalk and its executable inventory check.
"""

RETIRED_TEST_MODULES = {
    "test_adapter.py": 248,
    "test_code_builder_smoke.py": 5,
    "test_dashboard_v2_api.py": 39,
    "test_governed_chat_adapter.py": 6,
    "test_intent_api.py": 25,
    "test_live_governed_chat_contract.py": 1,
    "test_parity.py": 10,
    "test_reliability.py": 10,
    "test_research_builder_smoke.py": 12,
    "test_summaries.py": 62,
}

collect_ignore = list(RETIRED_TEST_MODULES)
