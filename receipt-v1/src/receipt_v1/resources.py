# SPDX-License-Identifier: Apache-2.0
"""Resource access for bundled files (schema, etc.).

The JSON Schema ships inside the wheel at receipt_v1/receipt.schema.json.
Use schema_path() to get the filesystem path for validation libraries,
or schema_json() to get the parsed dict directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).parent


def schema_path() -> Path:
    """Return the path to the bundled receipt.schema.json.

    Works whether installed via pip or run from the repo.
    """
    # In-package (wheel install)
    p = _PACKAGE_DIR / "receipt.schema.json"
    if p.exists():
        return p
    # Repo layout: schema/ is a sibling of src/
    repo_p = _PACKAGE_DIR.parent.parent.parent / "schema" / "receipt.schema.json"
    if repo_p.exists():
        return repo_p
    raise FileNotFoundError("receipt.schema.json not found in package or repo")


def schema_json() -> dict[str, Any]:
    """Load and return the receipt schema as a dict."""
    with open(schema_path()) as f:
        return json.load(f)
