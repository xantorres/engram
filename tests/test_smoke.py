from engram import __version__


def test_version_is_set():
    assert __version__
    assert __version__.count(".") >= 2


def test_cli_app_imports():
    from engram.cli.main import app

    assert app is not None


def test_default_store_dir_is_absolute():
    from engram.config import default_store_dir

    assert default_store_dir().is_absolute()
