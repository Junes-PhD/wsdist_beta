"""Native item models generated from the local FFXI DAT files by POLUtils."""

from __future__ import annotations

import json
from pathlib import Path


_catalog = Path(__file__).with_name("polutils_models.json")
try:
    POLUTILS_MODELS = json.loads(_catalog.read_text(encoding="utf-8")).get("models", {})
except (OSError, ValueError, TypeError):
    POLUTILS_MODELS = {}
