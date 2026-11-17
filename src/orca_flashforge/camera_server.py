"""Private, loopback-only origin for WebKit's MediaSource objects.

Runs inside the already isolated worker. Camera URLs never reach this server.
"""

import http.server
import os
import secrets
import selectors
import shutil
import subprocess
import sysconfig
import tempfile
import threading
from importlib.resources import files

from .model import FlashForgeError, camera_url


class CameraServer:
    def __init__(self):
        token = secrets.token_urlsafe(32)
        path = '/' + token
        resources = files('orca_flashforge').joinpath('ui')
        library = resources.joinpath('vendor/hls-1.5.6.min.js').read_text(encoding='utf-8')
        if '</script' in library.lower():
            raise ValueError('Invalid HLS library')
        html = resources.joinpath('camera.html').read_text(encoding='utf-8').replace('__HLS_LIBRARY__', library).encode()
        self._lock = threading.Lock()
        self._streams = {}
        self._processes = set()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                    self.send_error(404)
                    return
                if self.path.startswith(path + '/source/'):
                    with owner._lock:
                        source = owner._streams.get(self.path.rsplit('/', 1)[-1])
                    if not source:
                        self.send_error(404)
                        return
                    self.send_response(302)
                    self.send_header('Location', source)
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    return
                if self.path.startswith(path + '/mjpeg/'):
                    stream_id = self.path.rsplit('/', 1)[-1]
                    with owner._lock:
                        exists = stream_id in owner._streams
                    if not exists:
                        self.send_error(404)
                        return
                    owner._serve_mjpeg(self, path, stream_id)
                    return
                if self.path != path:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; "
                                 "style-src 'unsafe-inline'; connect-src http: https:; media-src blob: http: https:")
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}{path}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name='FlashForge-camera')
        self.thread.start()

    def fallback(self, source):
        if not camera_url(source):
            raise FlashForgeError('La impresora no ha facilitado una dirección de cámara válida.')
        codec_dir = os.path.join('/usr/lib', sysconfig.get_config_var('MULTIARCH') or '', 'codecs-extra/lib')
        if not (shutil.which('gst-launch-1.0') and os.path.isdir(codec_dir)) and not shutil.which('ffmpeg'):
            raise FlashForgeError('No se encontró un reproductor auxiliar compatible en el entorno de OrcaSlicer.')
        self.stop_streams()
        stream_id = secrets.token_urlsafe(32)
        with self._lock:
            self._streams[stream_id] = source
        return self.url + '/mjpeg/' + stream_id

    def _serve_mjpeg(self, handler, path, stream_id):
        source = f'http://127.0.0.1:{self.server.server_port}{path}/source/{stream_id}'
        codec_dir = os.path.join('/usr/lib', sysconfig.get_config_var('MULTIARCH') or '', 'codecs-extra/lib')
        environment = os.environ.copy()
        if shutil.which('gst-launch-1.0') and os.path.isdir(codec_dir):
            # Only the isolated player gets the Flatpak's matching codec libraries.
            environment['LD_LIBRARY_PATH'] = codec_dir + (':' + environment['LD_LIBRARY_PATH'] if environment.get('LD_LIBRARY_PATH') else '')
            environment['GST_REGISTRY'] = os.path.join(tempfile.gettempdir(), f'orca-flashforge-camera-gst-{os.getuid()}.bin')
            environment['GST_PLUGIN_FEATURE_RANK'] = 'avdec_h264:512'
            command = ['gst-launch-1.0', '-q', 'uridecodebin', 'uri=' + source, '!', 'queue',
                       'max-size-buffers=2', 'max-size-bytes=0', 'max-size-time=0', 'leaky=downstream', '!',
                       'videoconvert', '!', 'videorate', 'drop-only=true', 'max-rate=12', '!',
                       'jpegenc', 'quality=70', '!', 'multipartmux', 'boundary=orca_frame', '!', 'fdsink', 'fd=1', 'sync=false']
            # Keep the auxiliary decoder from consuming every core on large hosts.
            if shutil.which('taskset') and hasattr(os, 'sched_getaffinity'):
                try:
                    cpus = sorted(os.sched_getaffinity(0))[:2]
                    if cpus:
                        command = ['taskset', '-c', ','.join(map(str, cpus))] + command
                except OSError:
                    pass
            boundary = 'orca_frame'
        else:
            command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                       '-protocol_whitelist', 'http,https,tcp,tls,crypto', '-rw_timeout', '15000000',
                       '-i', source, '-an', '-vf', 'fps=12', '-threads', '2', '-c:v', 'mjpeg',
                       '-q:v', '7', '-f', 'mpjpeg', 'pipe:1']
            boundary = 'ffmpeg'
        process = None
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, env=environment)
            with self._lock:
                if stream_id not in self._streams:
                    return
                self._processes.add(process)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(25):
                    handler.send_error(504)
                    return
                first = process.stdout.read(65536)
                if not first:
                    handler.send_error(502)
                    return
                handler.send_response(200)
                handler.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=' + boundary)
                handler.send_header('Cache-Control', 'no-store')
                handler.end_headers()
                handler.wfile.write(first)
                while selector.select(25):
                    chunk = process.stdout.read(65536)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
        except (OSError, BrokenPipeError):
            pass
        finally:
            if process:
                try:
                    if process.poll() is None:
                        process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if process.stdout:
                    process.stdout.close()
                with self._lock:
                    self._processes.discard(process)

    def stop_streams(self):
        with self._lock:
            self._streams.clear()
            processes = list(self._processes)
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
            except ProcessLookupError:
                pass

    def close(self):
        self.stop_streams()
        self.server.shutdown()
        self.server.server_close()
