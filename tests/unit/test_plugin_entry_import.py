"""Regression tests for the plugin entry's package and pytest import modes."""

import importlib.util
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[2]


def test_entrypoint_imported_as_top_level_module_is_inert():
    """Pytest's root __init__.py import must not resolve host-only modules."""
    spec = importlib.util.spec_from_file_location("_advisor_top_level_entry", ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert callable(module.register)
    assert not hasattr(module.register, "__wrapped__")


def test_entrypoint_forwards_register_via_package_relative_import():
    """Real package imports retain the public register callable semantics."""
    package_name = "_synthetic_advisor_plugin"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    host = types.ModuleType(f"{package_name}.advisor_host")
    sentinel = object()
    host.register = lambda ctx: sentinel
    previous = {name: sys.modules.get(name) for name in (package_name, f"{package_name}.advisor_host")}
    try:
        sys.modules[package_name] = package
        sys.modules[f"{package_name}.advisor_host"] = host
        spec = importlib.util.spec_from_file_location(package_name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module

        spec.loader.exec_module(module)

        assert module.register(object()) is sentinel
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
