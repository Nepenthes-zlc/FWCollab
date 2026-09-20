from __future__ import annotations


def test_package_import_has_development_version() -> None:
    import fwcollab

    assert fwcollab.__version__ == "0.1.0.dev0"


def test_cli_module_imports_without_optional_dependencies() -> None:
    from fwcollab import cli

    assert callable(cli.main)

