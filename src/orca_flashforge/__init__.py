"""FlashForge integration for OrcaSlicer and its isolated SDK worker."""

__version__ = "0.1.0"

try:
    import orca as _orca
except ModuleNotFoundError as error:
    if error.name != "orca":
        raise
else:
    if hasattr(_orca, "plugin"):
        try:
            from .plugin import FlashForgePlugin  # noqa: F401
        except BaseException as error:
            # Orca's plugin loader currently omits the Python exception details in
            # its failure message. Preserve them in the exception text for its log.
            raise RuntimeError(
                f"FlashForge module import failed: {type(error).__name__}: {error!r}"
            ) from error
    else:
        FlashForgePlugin = None
