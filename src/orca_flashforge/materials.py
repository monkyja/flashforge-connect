"""Translate received LFS slots to Orca's presentation format, never to tool numbers."""

import re

from .model import FlashForgeError


def color_rgb(value):
    value = str(value or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value):
        return "#" + value[:6].upper()
    return ""


def slot_table(status):
    slots = status.get("slots", [])
    ids = [slot.get("id") for slot in slots]
    if any(type(i) is not int or i < 0 for i in ids) or len(set(ids)) != len(ids):
        raise FlashForgeError("El LFS devolvió identificadores de ranura no válidos.")
    return sorted(slots, key=lambda slot: slot["id"])


def ams_payload(slots):
    # Four trays per presentation unit is Orca's ParseV1_0 contract. The reverse
    # table retains the actual SDK IDs; these indices never go to FlashForge.
    if len(slots) > 16:
        raise FlashForgeError("La vista nativa admite aquí hasta 16 ranuras; usa la página FlashForge.")
    units, present = [], 0
    for index, slot in enumerate(slots):
        if index % 4 == 0:
            units.append({"id": str(index // 4), "info": "0002", "display_name": "FlashForge LFS",
                          "tray": []})
        occupied = slot["has_filament"]
        if occupied:
            present |= 1 << index
        rgb = color_rgb(slot["color"])
        tray = {"id": str(index % 4), "tray_info_idx": "",
                "tray_type": slot["material"] if occupied else "",
                "tray_color": rgb[1:] + "FF" if rgb and occupied else "00000000"}
        if not occupied:
            tray["tray_slot_placeholder"] = "1"
        units[-1]["tray"].append(tray)
    return {"ams": units, "ams_exist_bits": format((1 << len(units)) - 1, "X"),
            "tray_exist_bits": format(present, "X")}


def suggest_mappings(filaments, slots):
    """Suggest only an exact material/color match with a single occupied slot."""
    mappings = []
    for filament in filaments:
        rgb = color_rgb(filament.get("color"))
        material = filament.get("material", "").strip().casefold()
        matches = [slot for slot in slots if slot["has_filament"] and rgb and material
                   and slot["material"].strip().casefold() == material and color_rgb(slot["color"]) == rgb]
        if len(matches) == 1:
            slot = matches[0]
            mappings.append({"toolId": filament["toolId"], "slotId": slot["id"],
                             "materialName": slot["material"], "toolMaterialColor": rgb,
                             "slotMaterialColor": slot["color"]})
    return mappings
