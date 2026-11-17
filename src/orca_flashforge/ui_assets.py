"""Build the self-contained page passed to OrcaSlicer's WebView."""

import base64
from importlib.resources import files
from . import __version__


def render_ui():
    resources = files("orca_flashforge").joinpath("ui")
    icon = "data:image/svg+xml;base64," + base64.b64encode(resources.joinpath("flashforge.svg").read_bytes()).decode()
    return (resources.joinpath("device.html").read_text(encoding="utf-8")
            .replace("__FLASHFORGE_ICON__", icon).replace("__PLUGIN_VERSION__", __version__))
