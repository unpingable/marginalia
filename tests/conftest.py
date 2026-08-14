# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures for Marginalia tests.

The retained donor suites explicitly opt into quarantined historical routes.
Product-surface regressions turn this switch off and assert the normal M1
boundary.
"""

import os

os.environ.setdefault("MARGINALIA_ENABLE_DONOR_ROUTES", "1")
