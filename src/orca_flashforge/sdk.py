"""Official FlashNetwork 3.4.0 ABI. Import and use only in sdk_worker.

Structures follow FlashForge/Orca-Flashforge develop at ea9a451.
The matching header lives in native/FlashNetwork-3.4.0.h.
"""

import ctypes as c
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .model import FlashForgeError, camera_url, normalize

S = c.c_char_p
I = c.c_int
D = c.c_double


class LogSettings(c.Structure):
    _fields_ = [("fileDir", S), ("expireHours", I), ("level", I)]


class Token(c.Structure):
    _fields_ = [("expiresIn", I), ("accessToken", S), ("refreshToken", S)]


class UserProfile(c.Structure):
    _fields_ = [(key, S) for key in ("uid", "nickname", "headImgUrl", "email")]


class AuthenticationError(FlashForgeError):
    """The server rejected the saved credentials, rather than a network failure."""


class UpdateInfo(c.Structure):
    _fields_ = [(key, S) for key in ("status", "title", "content", "tips")]


class WanDevice(c.Structure):
    _fields_ = [(key, S) for key in ("devId", "name", "model", "imageUrl", "status", "location",
                                     "serialNumber", "devTopic")] + [("updateInfo", UpdateInfo)]


class TimeLapseVideo(c.Structure):
    _fields_ = [(key, S) for key in ("jobId", "fileName", "videoUrl", "thumbUrl")] + [("width", I), ("height", I)]


class Slot(c.Structure):
    _fields_ = [("slotId", I), ("hasFilament", I), ("materialName", S), ("materialColor", S)]


class Station(c.Structure):
    _fields_ = [(key, I) for key in ("slotCnt", "currentSlot", "currentLoadSlot", "stateAction", "stateStep")] + [
        ("slotInfos", c.POINTER(Slot))]


class IndependentMaterial(c.Structure):
    _fields_ = [("stateAction", I), ("stateStep", I), ("materialName", S), ("materialColor", S)]


class Detail(c.Structure):
    _fields_ = (
        [("devId", S), ("pid", I), ("nozzleCnt", I), ("nozzleStyle", I)]
        + [(key, S) for key in ("measure", "nozzleModel", "firmwareVersion", "macAddr", "ipAddr", "name")]
        + [(key, I) for key in ("lidar", "camera", "moveCtrl", "extrudeCtrl")]
        + [("location", S), ("status", S), ("coordinate", D * 3)]
        + [(key, S) for key in ("jobId", "printFileName", "printFileThumbUrl")]
        + [("printLayer", I), ("targetPrintLayer", I), ("printProgress", D)]
        + [(key, D) for key in ("rightTemp", "rightTargetTemp", "leftTemp", "leftTargetTemp")]
        + [("nozzleTemps", c.POINTER(D)), ("nozzleTargetTemps", c.POINTER(D))]
        + [(key, D) for key in ("platTemp", "platTargetTemp", "chamberTemp", "chamberTargetTemp",
                                 "fillAmount", "zAxisCompensation")]
        + [("rightFilamentType", S), ("leftFilamentType", S)]
        + [(key, D) for key in ("currentPrintSpeed", "printSpeedAdjust", "printDuration", "estimatedTime",
                                 "estimatedRightLen", "estimatedLeftLen", "estimatedRightWeight",
                                 "estimatedLeftWeight", "coolingFanSpeed", "coolingFanLeftSpeed", "chamberFanSpeed")]
        + [(key, I) for key in ("hasRightFilament", "hasLeftFilament", "hasMatlStation")]
        + [("matlStationInfo", Station), ("indepMatlInfo", IndependentMaterial)]
        + [(key, S) for key in ("internalFanStatus", "externalFanStatus", "clearFanStatus", "doorStatus",
                                 "lightStatus", "autoShutdown")]
        + [(key, D) for key in ("autoShutdownTime", "tvoc", "remainingDiskSpace", "cumulativePrintTime", "cumulativeFilament")]
        + [(key, S) for key in ("cameraStreamUrl", "polarRegisterCode", "flashRegisterCode", "errorCode")]
    )


def decode(value):
    return value.decode("utf-8", errors="replace") if value else ""


def probe(library):
    try:
        lib = c.CDLL(str(Path(library).expanduser().resolve(strict=True)))
        lib.fnet_getVersion.restype = S
        lib.fnet_getVersion.argtypes = []
        return lib, decode(lib.fnet_getVersion())
    except (OSError, AttributeError):
        raise FlashForgeError("No se pudo cargar libFlashNetwork.so o faltan sus dependencias.") from None


class CloudSession:
    def __init__(self, library, settings):
        self.lib, self.version = probe(library)
        if self.version != "3.4.0":
            raise FlashForgeError(f"SDK {self.version} no validado. Esta versión admite FlashNetwork 3.4.0.")
        settings_path = Path(settings).expanduser().resolve(strict=True)
        self._temporary = tempfile.TemporaryDirectory(prefix="orca-flashforge-")
        self._initialized = False
        self.access = b""
        self.refresh = b""
        self.deadline = 0
        self._devices = {}
        self._transport = None
        self._token_lock = threading.RLock()
        self._token_sink = None
        self.client_id = str(uuid.uuid4()).encode()
        self._bind()
        log = LogSettings(str(self._temporary.name).encode(), 1, 0)
        self._check(self.lib.fnet_initlize(str(settings_path).encode(), c.byref(log)))
        self._initialized = True
        self.lib.fnet_setUserAgent(b"OrcaFlashForge/0.1.0 (Linux; OrcaSlicer plugin)")

    def _bind(self):
        def bind(name, restype, *args):
            function = getattr(self.lib, name)
            function.restype = restype
            function.argtypes = list(args)
        bind("fnet_initlize", I, S, c.POINTER(LogSettings))
        bind("fnet_uninitlize", None)
        bind("fnet_setUserAgent", None, S)
        bind("fnet_getTokenByPassword", I, S, S, S, c.POINTER(c.POINTER(Token)), c.POINTER(c.c_void_p), I)
        bind("fnet_refreshToken", I, S, S, c.POINTER(c.POINTER(Token)), c.POINTER(c.c_void_p), I)
        bind("fnet_freeToken", None, c.POINTER(Token))
        bind("fnet_freeString", None, c.c_void_p)
        bind("fnet_getUserProfile", I, S, c.POINTER(c.POINTER(UserProfile)), I)
        bind("fnet_freeUserProfile", None, c.POINTER(UserProfile))
        bind("fnet_getWanDevList", I, S, S, c.c_longlong, c.c_longlong,
             c.POINTER(c.POINTER(WanDevice)), c.POINTER(I), I)
        bind("fnet_freeWanDevList", None, c.POINTER(WanDevice), I)
        bind("fnet_getWanDevProductDetail", I, S, S, S, c.POINTER(c.c_void_p), c.POINTER(c.POINTER(Detail)), I)
        bind("fnet_freeDevProduct", None, c.c_void_p)
        bind("fnet_freeDevDetail", None, c.POINTER(Detail))

    @staticmethod
    def _check(code):
        if code != 0:
            labels = {2001: "Sesión caducada; inicia sesión de nuevo.",
                      2002: "FlashForge rechazó el usuario o la contraseña.",
                      1001: "La impresora rechazó las credenciales LAN."}
            error = AuthenticationError if code in (2001, 2002) else FlashForgeError
            raise error(labels.get(code, f"FlashNetwork devolvió el código {code}."))

    def _read_token(self, call):
        token = c.POINTER(Token)()
        message = c.c_void_p()
        try:
            self._check(call(c.byref(token), c.byref(message)))
            if not token or not token.contents.accessToken or token.contents.expiresIn <= 0:
                raise FlashForgeError("FlashForge no devolvió una sesión válida.")
            self.access = bytes(token.contents.accessToken)
            self.refresh = bytes(token.contents.refreshToken or b"")
            self.deadline = time.monotonic() + token.contents.expiresIn
            if self._token_sink:
                self._token_sink(self.refresh)
        finally:
            if token:
                self.lib.fnet_freeToken(token)
            if message:
                self.lib.fnet_freeString(message)

    def login(self, username, password):
        self.access = self.refresh = b""
        self.deadline = 0
        if not username or not password:
            raise FlashForgeError("Introduce el usuario y la contraseña de FlashForge.")
        self._read_token(lambda out, msg: self.lib.fnet_getTokenByPassword(
            username.encode(), password.encode(), b"es", out, msg, 10000))
        return self._login_result(username)

    def restore(self, refresh_token, username):
        if not isinstance(refresh_token, str) or not refresh_token:
            raise AuthenticationError("La sesión guardada no es válida. Vuelve a conectar tu cuenta.")
        self._read_token(lambda out, msg: self.lib.fnet_refreshToken(
            refresh_token.encode(), b"es", out, msg, 10000))
        return self._login_result(username)

    def _login_result(self, username):
        try:
            profile = self.profile()
        except FlashForgeError:
            profile = {"nickname": username, "email": "", "avatar_url": ""}
        return {"connected": True, "sdk_version": self.version, "profile": profile}

    def profile(self):
        self._authenticate()
        profile = c.POINTER(UserProfile)()
        try:
            self._check(self.lib.fnet_getUserProfile(self.access, c.byref(profile), 10000))
            if not profile:
                raise FlashForgeError("FlashForge no devolvió el perfil de la cuenta.")
            return {"nickname": decode(profile.contents.nickname)[:512], "email": decode(profile.contents.email)[:512],
                    "avatar_url": camera_url(decode(profile.contents.headImgUrl))}
        finally:
            if profile:
                self.lib.fnet_freeUserProfile(profile)

    def _authenticate(self):
        with self._token_lock:
            if not self.access:
                raise FlashForgeError("Inicia sesión en FlashForge.")
            if time.monotonic() >= self.deadline - 60:
                if not self.refresh:
                    raise FlashForgeError("Sesión caducada; inicia sesión de nuevo.")
                self._read_token(lambda out, msg: self.lib.fnet_refreshToken(self.refresh, b"es", out, msg, 10000))

    def devices(self):
        self._authenticate()
        devices = c.POINTER(WanDevice)()
        count = I()
        try:
            self._check(self.lib.fnet_getWanDevList(self.client_id, self.access, 31, 15,
                                                  c.byref(devices), c.byref(count), 10000))
            if not 0 <= count.value <= 1000 or (count.value and not devices):
                raise FlashForgeError("Lista de impresoras no válida.")
            self._devices = {decode(devices[i].devId): {key: decode(getattr(devices[i], key))
                             for key in ("serialNumber", "devTopic")} for i in range(count.value)}
            return [{**{key: decode(getattr(devices[i], key)) for key in ("devId", "name", "model", "status")},
                     "image_url": camera_url(decode(devices[i].imageUrl))}
                    for i in range(count.value)]
        finally:
            if devices:
                self.lib.fnet_freeWanDevList(devices, count.value)

    def status(self, device_id):
        self._authenticate()
        if not device_id:
            raise FlashForgeError("Selecciona una impresora de tu cuenta.")
        product = c.c_void_p()
        detail = c.POINTER(Detail)()
        try:
            self._check(self.lib.fnet_getWanDevProductDetail(self.client_id, self.access, device_id.encode(),
                                                           c.byref(product), c.byref(detail), 10000))
            if not detail:
                raise FlashForgeError("El SDK no devolvió el estado de la impresora.")
            value = detail.contents
            raw = {key: decode(getattr(value, key)) for key in
                   ("name", "firmwareVersion", "status", "printFileName", "cameraStreamUrl", "errorCode")}
            raw["pid"] = value.pid
            raw["moveCtrl"] = value.moveCtrl
            for key in ("printProgress", "printLayer", "targetPrintLayer", "printDuration", "estimatedTime",
                        "platTemp", "platTargetTemp", "chamberTemp", "chamberTargetTemp"):
                raw[key] = getattr(value, key)
            if not 0 <= value.nozzleCnt <= 16 or not 0 <= value.matlStationInfo.slotCnt <= 64:
                raise FlashForgeError("El SDK devolvió un número de boquillas o ranuras no válido.")
            for key in ("nozzleTemps", "nozzleTargetTemps"):
                pointer = getattr(value, key)
                if pointer:
                    raw[key] = [pointer[i] for i in range(value.nozzleCnt)]
            if "nozzleTemps" not in raw:
                for key in ("rightTemp", "rightTargetTemp", "leftTemp", "leftTargetTemp"):
                    raw[key] = getattr(value, key)
            station = value.matlStationInfo
            if station.slotInfos:
                raw["matlStationInfo"] = {"slotInfos": [
                    {"slotId": station.slotInfos[i].slotId, "hasFilament": station.slotInfos[i].hasFilament,
                     "materialName": decode(station.slotInfos[i].materialName),
                     "materialColor": decode(station.slotInfos[i].materialColor)} for i in range(station.slotCnt)]}
            return normalize(raw, mode="cloud")
        finally:
            if detail:
                self.lib.fnet_freeDevDetail(detail)
            if product:
                self.lib.fnet_freeDevProduct(product)

    def recordings(self, device_id):
        self._authenticate()
        if device_id not in self._devices:
            raise FlashForgeError("Selecciona una impresora de tu cuenta.")
        try:
            get = self.lib.fnet_getWanDevTimeLapseVideoList
            free = self.lib.fnet_freeTimeLapseVideoList
        except AttributeError:
            raise FlashForgeError("El SDK no admite consultar grabaciones.") from None
        get.restype, get.argtypes = I, [S, S, S, I, c.POINTER(c.POINTER(TimeLapseVideo)), c.POINTER(I), I]
        free.restype, free.argtypes = None, [c.POINTER(TimeLapseVideo), I]
        videos, count = c.POINTER(TimeLapseVideo)(), I()
        try:
            self._check(get(self.client_id, self.access, device_id.encode(), 15, c.byref(videos), c.byref(count), 10000))
            if not 0 <= count.value <= 100 or (count.value and not videos):
                raise FlashForgeError("La lista de grabaciones no es válida.")
            return [{"name": decode(videos[i].fileName)[:512], "url": camera_url(decode(videos[i].videoUrl)),
                     "thumbnail": camera_url(decode(videos[i].thumbUrl))} for i in range(count.value)]
        finally:
            if videos:
                free(videos, count.value)

    def close(self):
        if self._transport:
            self._transport.close()
            self._transport = None
        self._devices.clear()
        self._token_sink = None
        self.access = self.refresh = b""
        if self._initialized:
            self.lib.fnet_uninitlize()
            self._initialized = False
        self._temporary.cleanup()
