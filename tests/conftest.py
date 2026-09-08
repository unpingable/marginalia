# SPDX-License-Identifier: Apache-2.0
"""Shared test configuration for the ag-ng-only Marginalia product.

The ignored modules are frozen tests for removed classic-daemon and non-fiction
donor surfaces. Their history remains in Git; they are not a release dependency.
"""

collect_ignore = [
    "test_adapter.py",
    "test_code_builder_smoke.py",
    "test_dashboard_v2_api.py",
    "test_governed_chat_adapter.py",
    "test_intent_api.py",
    "test_live_governed_chat_contract.py",
    "test_parity.py",
    "test_reliability.py",
    "test_research_builder_smoke.py",
    "test_summaries.py",
]
