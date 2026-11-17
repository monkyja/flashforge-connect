"""Diagnostics and read-only access without running OrcaSlicer."""

import argparse
import getpass
import json
import sys

from .bridge import SdkBridge
from .lan import LanClient
from .model import FlashForgeError, demo_status


def main():
    parser = argparse.ArgumentParser(description="Diagnóstico de OrcaFlashForge (solo consultas)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--library", required=True)
    cloud = commands.add_parser("cloud")
    cloud.add_argument("--library", required=True)
    cloud.add_argument("--settings", required=True)
    cloud.add_argument("--username", required=True)
    cloud.add_argument("--device", default="", help="Sin este argumento, lista las impresoras")
    lan = commands.add_parser("lan")
    lan.add_argument("--host", required=True)
    lan.add_argument("--serial", required=True)
    args = parser.parse_args()
    bridge = SdkBridge(sys.executable)
    try:
        if args.command == "demo":
            result = demo_status()
        elif args.command == "doctor":
            result = bridge.request("probe", library=args.library)
        elif args.command == "cloud":
            password = getpass.getpass("Contraseña de FlashForge: ")
            bridge.request("login", library=args.library, settings=args.settings,
                           username=args.username, password=password)
            password = None
            result = bridge.request("status", device_id=args.device) if args.device else bridge.request("devices")
        else:
            code = getpass.getpass("Código de acceso LAN: ")
            result = LanClient(args.host, args.serial, code).status()
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except FlashForgeError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
