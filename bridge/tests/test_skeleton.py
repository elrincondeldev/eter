import importlib

import pytest


@pytest.mark.parametrize("name", ["eter_core", "eter_mcp", "eter_cli", "eter_api", "eter_sim"])
def test_package_imports(name: str) -> None:
    importlib.import_module(name)
