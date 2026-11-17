"""Install the collection into an isolated Maki data directory, without Git/network."""

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def install_package(root):
    package = root / "data/maki/site/pack/tests/start/maki-plugins"
    package.mkdir(parents=True)
    for directory in ("plugin", "lua"):
        shutil.copytree(ROOT / directory, package / directory)
    shutil.copyfile(ROOT / "plugin.toml", package / "plugin.toml")
    return package
