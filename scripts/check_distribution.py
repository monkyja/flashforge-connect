"""Check the built plugin and the optional release tag before distribution."""

import os
from pathlib import Path
import tomllib
from zipfile import ZipFile

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
tag = os.environ.get("RELEASE_REF", "")
if tag.startswith("refs/tags/") and tag != f"refs/tags/v{version}":
    raise SystemExit("La etiqueta no coincide con la versión del paquete.")
wheels = list((root / "dist").glob(f"orca_flashforge-{version}-*.whl"))
if len(wheels) != 1:
    raise SystemExit("Debe existir exactamente un wheel de la versión actual.")
with ZipFile(wheels[0]) as wheel:
    names = wheel.namelist()
    required = {"orca_flashforge/__init__.py", "orca_flashforge/ui/device.html"}
    if not required.issubset(names):
        raise SystemExit("El wheel no contiene el plugin y su interfaz.")
    if any(name.lower().endswith((".so", ".dll", ".dylib", ".dat")) for name in names):
        raise SystemExit("El wheel contiene binarios o archivos DAT prohibidos.")
print(f"Wheel verificado: {wheels[0].name}")
