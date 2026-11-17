"""Cloud job payloads reproduced from FlashNetwork 3.4.0 serializers.

These functions construct data only. Upload, broker authentication and dispatch
are separate operations and are not enabled until their contracts are verified.
"""

from .model import FlashForgeError


def _options(options):
    mappings = [{"tNum": item["toolId"], "slot": item["slotId"], "mt": item["materialName"],
                 "sRgb": item["toolMaterialColor"], "tRgb": item["slotMaterialColor"]}
                for item in options["material_mappings"]]
    return mappings


def create_job_payload(job, options, storage):
    mappings = _options(options)
    return {"deviceID": [job["device_id"]], "bucket": storage["bucket"], "endpoint": storage["endpoint"],
            "fileMeta": {"name": job["name"], "contentType": job["type"], "md5": job["md5"],
                         "size": job["size"], "storageUrl": storage["file_url"], "storageKey": storage["file_key"]},
            "thumbMeta": {"name": job["thumbnail_name"], "contentType": "png", "md5": job["thumbnail_md5"],
                          "size": job["thumbnail_size"], "storageUrl": storage["thumb_url"], "storageKey": storage["thumb_key"]},
            "printNow": True, "levelingBeforePrint": options["bed_leveling"], "flowCalibration": options.get("flow_calibration", False),
            "firstLayerInspection": False, "timeLapseVideo": options["timelapse"],
            "useMs": options["use_material_station"], "tCount": len(mappings), "ms": mappings,
            "unionId": job["union_id"], "plateNo": job["plate"] - 1}


def start_job_payload(job, options, storage, serial_number, job_id):
    mappings = _options(options)
    return {"cmd": "newJob_cmd", "jobInfo": [[serial_number, job_id]],
            "args": {"filename": job["name"], "fileMd5": job["md5"], "filepath": storage["file_url"],
                     "thumbPath": storage["thumb_url"], "printNow": True, "leveling": options["bed_leveling"],
                     "flowCalibration": options.get("flow_calibration", False), "firstLayerInspection": False,
                     "timeLapseVideo": options["timelapse"], "useMs": options["use_material_station"],
                     "tCount": len(mappings), "ms": mappings}}


def existing_job_payload(device_id, filename, options):
    mappings = _options(options)
    return {"deviceID": device_id, "fileName": filename, "printNow": True,
            "leveling": options["bed_leveling"], "flowCalibration": options.get("flow_calibration", False),
            "firstLayerInspection": False, "timeLapseVideo": options["timelapse"],
            "useMs": options["use_material_station"], "tCount": len(mappings), "ms": mappings}


def start_existing_job_payload(filename, job_id, thumbnail, options):
    create = existing_job_payload("", filename, options)
    return {"cmd": "newLocalJob_cmd", "args": {"jobId": job_id, "thumbPath": thumbnail,
            **{key: value for key, value in create.items() if key != "deviceID"}}}


def created_job_id(response, device_id):
    """Interpret the SDK's startResult/startError maps without retrying."""
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict) or response.get("code") != 0:
        raise FlashForgeError("No se pudo confirmar la creación del trabajo. Comprueba la impresora antes de repetir.")
    results, errors = data.get("startResult"), data.get("startError")
    if not isinstance(results, dict) or not isinstance(errors, dict):
        raise FlashForgeError("No se pudo confirmar la creación del trabajo. Comprueba la impresora antes de repetir.")
    if device_id in errors:
        raise FlashForgeError("FlashForge rechazó la creación del trabajo para esta impresora.")
    result = results.get(device_id)
    if not isinstance(result, str) or not result:
        raise FlashForgeError("No se pudo confirmar la creación del trabajo. Comprueba la impresora antes de repetir.")
    return result
