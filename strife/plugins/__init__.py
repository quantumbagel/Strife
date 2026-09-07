"""Game plugin lifecycle: manifests, install, uninstall, discovery.

Game packages declare install-time data in ``plugin.toml``. The host reads that
file *before* importing the package so extras can be installed first.
``check_dependencies`` never installs anything; it only gates import.
"""

from strife.plugins.deps import check_dependencies, distribution_name
from strife.plugins.errors import PluginError
from strife.plugins.manager import PluginManager
from strife.plugins.manifest import PluginManifest, PluginRecord, load_manifest

__all__ = [
    "PluginError",
    "PluginManager",
    "PluginManifest",
    "PluginRecord",
    "check_dependencies",
    "distribution_name",
    "load_manifest",
]
