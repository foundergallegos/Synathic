"""Keep this opt-in example out of the default test run."""
import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SYNATHIC_SMARTMEMORY_EXAMPLE") == "1":
        return
    skip = pytest.mark.skip(reason="opt-in example: set SYNATHIC_SMARTMEMORY_EXAMPLE=1 (needs Docker)")
    for item in items:
        if str(item.fspath).startswith(os.path.dirname(__file__)):
            item.add_marker(skip)
