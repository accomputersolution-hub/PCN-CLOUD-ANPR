from __future__ import annotations

"""RTSP frame capture via a continuous FFmpeg decode process.

Never log the full RTSP URL (may contain credentials). Use redact_url helpers.
"""

import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse

from pcn_edge.frames import Frame, FrameSource

logger = logging.getLogger(__name__)

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"
# Incomplete / gray decoder garbage from older MPEG-4 cameras is often tiny.
DEFAULT_MIN_JPEG_BYTES = 1500
DEFAULT_WARMUP_FRAMES = 8
# Near-black / flat gray after decode (OpenCV path only).
_LUMA_BLACK_MAX = 8.0
_LUMA_GRAY_MIN = 110.0
_LUMA_GRAY_MAX = 145.0
_LUMA_FLAT_STD_MAX = 12.0


def find_ffmpeg() -> str | None:
    """Return absolute path to ffmpeg if discoverable on PATH, else None."""
    return shutil.which("ffmpeg")


def redact_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            netloc = f"***:***@{host}"
            return urlunparse((parsed.scheme, netloc, parsed.path, "", parsed.query, ""))
        return url
    except Exception:
        return "rtsp://***"


def extract_jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """Parse SOF0 height/width from a JPEG bitstream."""
    try:
        idx = data.find(b"\xff\xc0")
        if idx > 0 and idx + 9 < len(data):
            height = int.from_bytes(data[idx + 5 : idx + 7], "big")
            width = int.from_bytes(data[idx + 7 : idx + 9], "big")
            return width, height
    except Exception:
        pass
    return None, None


def is_valid_jpeg_frame(
    data: bytes,
    *,
    min_bytes: int = DEFAULT_MIN_JPEG_BYTES,
    check_luma: bool = True,
) -> bool:
    """Reject empty, truncated, or near-uniform gray/black decoded frames."""
    if not data or len(data) < min_bytes:
        return False
    soi = data.find(JPEG_SOI)
    if soi < 0 or soi > 16:
        return False
    payload = data[soi:]
    if payload.rfind(JPEG_EOI) < 0:
        return False
    if check_luma and not _luma_looks_valid(payload):
        return False
    return True


def _luma_looks_valid(data: bytes) -> bool:
    """Optional OpenCV mean/std check; skip (accept) if OpenCV unavailable."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return True

    try:
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            return False
        mean = float(img.mean())
        std = float(img.std())
        if mean < _LUMA_BLACK_MAX:
            return False
        # Flat mid-gray (classic incomplete MPEG-4 decode artifact).
        if _LUMA_GRAY_MIN <= mean <= _LUMA_GRAY_MAX and std < _LUMA_FLAT_STD_MAX:
            return False
        return True
    except Exception:
        return True


@dataclass
class CameraConfig:
    camera_id: str
    rtsp_url: str
    enabled: bool = True
    frame_interval: float = 0.5
    reconnect_min_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    connect_timeout_seconds: float = 15.0
    warmup_frames: int = DEFAULT_WARMUP_FRAMES
    min_jpeg_bytes: int = DEFAULT_MIN_JPEG_BYTES
    direction: str = "ENTRY"


@dataclass
class RTSPFrameSource(FrameSource):
    """Continuous FFmpeg RTSP decode: one long-lived process per camera.

    FFmpeg writes MJPEG to stdout; we parse SOI/EOI, discard warm-up and
    invalid frames, then sample at ``config.frame_interval`` (default 2 FPS).
    """

    config: CameraConfig
    _open: bool = False
    _seq: int = 0
    _last_error: str | None = None
    _last_read: float = 0.0
    _ffmpeg: str | None = None
    _proc: subprocess.Popen[bytes] | None = None
    _jpeg_queue: queue.Queue[bytes | None] = field(default_factory=lambda: queue.Queue(maxsize=8))
    _reader_thread: threading.Thread | None = None
    _stderr_thread: threading.Thread | None = None
    _warmup_remaining: int = 0
    _stop_reader: threading.Event = field(default_factory=threading.Event)
    resolution: str | None = field(default=None, init=False)
    _change_tracker: object | None = field(default=None, init=False)

    def open(self) -> None:
        if not self.config.rtsp_url:
            raise ConnectionError("RTSP URL is empty")
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise ConnectionError("FFmpeg is not installed or not on PATH")
        ok, msg = validate_rtsp_url(self.config.rtsp_url)
        if not ok:
            raise ConnectionError(msg)

        self._ffmpeg = ffmpeg
        if self._proc is not None or self._open:
            self.close()
        self._stop_reader.clear()
        self._warmup_remaining = max(0, int(self.config.warmup_frames))
        self._drain_queue()
        from pcn_edge.frame_diag import FrameChangeTracker

        self._change_tracker = FrameChangeTracker(warn_after=5)

        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            # Low-latency continuous RTSP (TCP). Avoid buffering / CFR frame duplication
            # that can make every sampled JPEG look identical on some IP cams (e.g. Zavio).
            "-fflags",
            "nobuffer+discardcorrupt",
            "-flags",
            "low_delay",
            "-rtsp_transport",
            "tcp",
            "-use_wallclock_as_timestamps",
            "1",
            "-i",
            self.config.rtsp_url,
            "-an",
            # FFmpeg 9+: do not CFR-duplicate the last picture into identical JPEGs.
            "-fps_mode",
            "passthrough",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-q:v",
            "5",
            "pipe:1",
        ]
        popen_kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL,
            "bufsize": 0,
        }
        if sys.platform == "win32":
            # Avoid Ctrl+C propagating; allow clean terminate of the child.
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        try:
            self._proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603 — ffmpeg path + URL
        except FileNotFoundError as exc:
            self._last_error = "FFmpeg not found"
            raise ConnectionError(self._last_error) from exc
        except OSError as exc:
            self._last_error = f"Failed to start FFmpeg: {exc}"
            raise ConnectionError(self._last_error) from exc

        self._open = True
        self._last_error = None
        self._reader_thread = threading.Thread(
            target=self._stdout_reader,
            name=f"ffmpeg-out-{self.config.camera_id[:8]}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_drainer,
            name=f"ffmpeg-err-{self.config.camera_id[:8]}",
            daemon=True,
        )
        self._reader_thread.start()
        self._stderr_thread.start()
        logger.info(
            "rtsp.open camera=%s url=%s ffmpeg=%s mode=continuous warmup=%s interval=%.3fs",
            self.config.camera_id,
            redact_url(self.config.rtsp_url),
            ffmpeg,
            self._warmup_remaining,
            self.config.frame_interval,
        )

    def close(self) -> None:
        self._open = False
        self._stop_reader.set()
        self._terminate_proc()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=1.0)
        self._reader_thread = None
        self._stderr_thread = None
        self._drain_queue()
        logger.info("rtsp.close camera=%s", self.config.camera_id)

    def _drain_queue(self) -> None:
        while True:
            try:
                self._jpeg_queue.get_nowait()
            except queue.Empty:
                break

    def _terminate_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                if sys.platform == "win32":
                    proc.terminate()
                else:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    if sys.platform == "win32":
                        proc.kill()
                    else:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError, OSError):
                            proc.kill()
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
        finally:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass

    def _stderr_drainer(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        redacted = redact_url(self.config.rtsp_url)
        try:
            while not self._stop_reader.is_set():
                line = proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                safe = text.replace(self.config.rtsp_url, redacted)
                logger.debug("ffmpeg.stderr camera=%s %s", self.config.camera_id, safe[:300])
        except Exception:
            pass

    def _stdout_reader(self) -> None:
        """Parse MJPEG from FFmpeg stdout into complete JPEG blobs."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._jpeg_queue.put(None)
            return
        buf = bytearray()
        try:
            while not self._stop_reader.is_set():
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                buf.extend(chunk)
                while True:
                    start = buf.find(JPEG_SOI)
                    if start < 0:
                        # Keep possible trailing 0xFF that might start SOI.
                        if buf and buf[-1] == 0xFF:
                            del buf[:-1]
                        else:
                            buf.clear()
                        break
                    if start > 0:
                        del buf[:start]
                    end = buf.find(JPEG_EOI, 2)
                    if end < 0:
                        # Cap runaway buffer if EOI never arrives.
                        if len(buf) > 8 * 1024 * 1024:
                            buf.clear()
                        break
                    end += 2
                    jpeg = bytes(buf[:end])
                    del buf[:end]
                    try:
                        self._jpeg_queue.put(jpeg, timeout=1.0)
                    except queue.Full:
                        # Drop oldest to keep latency low under backpressure.
                        try:
                            self._jpeg_queue.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            self._jpeg_queue.put_nowait(jpeg)
                        except queue.Full:
                            pass
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"FFmpeg stdout read failed: {exc}"
        finally:
            try:
                self._jpeg_queue.put(None, timeout=0.5)
            except queue.Full:
                pass

    @property
    def is_open(self) -> bool:
        return self._open and self._proc is not None and self._proc.poll() is None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _ffmpeg_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _take_latest_jpeg(self, first: bytes, timeout: float) -> bytes:
        """Keep only the newest JPEG waiting in the queue (drop stale buffered frames)."""
        latest = first
        dropped = 0
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and self._jpeg_queue.empty():
                break
            try:
                item = self._jpeg_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                # Put sentinel back for the next read() to observe process exit.
                try:
                    self._jpeg_queue.put_nowait(None)
                except queue.Full:
                    pass
                break
            latest = item
            dropped += 1
        if dropped:
            logger.debug(
                "rtsp.drop_stale camera=%s dropped=%s kept_bytes=%s",
                self.config.camera_id,
                dropped,
                len(latest),
            )
        return latest

    def read(self) -> Frame | None:
        if not self._open:
            return None

        now = time.monotonic()
        wait = self.config.frame_interval - (now - self._last_read)
        if self._last_read and wait > 0:
            time.sleep(wait)

        deadline = time.monotonic() + max(1.0, float(self.config.connect_timeout_seconds))
        discarded_invalid = 0
        while time.monotonic() < deadline:
            if not self._ffmpeg_alive() and self._jpeg_queue.empty():
                self._last_error = "FFmpeg process exited"
                raise ConnectionError(self._last_error)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = self._jpeg_queue.get(timeout=min(1.0, remaining))
            except queue.Empty:
                if not self._ffmpeg_alive():
                    self._last_error = "FFmpeg process exited"
                    raise ConnectionError(self._last_error)
                continue

            if item is None:
                self._last_error = "FFmpeg process exited"
                raise ConnectionError(self._last_error)

            if self._warmup_remaining > 0:
                self._warmup_remaining -= 1
                logger.debug(
                    "rtsp.warmup_discard camera=%s remaining=%s",
                    self.config.camera_id,
                    self._warmup_remaining,
                )
                continue

            # After warm-up: prefer the newest decoded JPEG (drop stale buffered copies).
            item = self._take_latest_jpeg(item, timeout=0.0)

            if not is_valid_jpeg_frame(item, min_bytes=self.config.min_jpeg_bytes):
                discarded_invalid += 1
                if discarded_invalid <= 3 or discarded_invalid % 25 == 0:
                    logger.debug(
                        "rtsp.invalid_frame camera=%s size=%s discarded=%s",
                        self.config.camera_id,
                        len(item),
                        discarded_invalid,
                    )
                continue

            # Copy so downstream cannot share a mutated buffer
            payload = bytes(item)
            self._seq += 1
            self._last_read = time.monotonic()
            self._last_error = None
            width, height = extract_jpeg_dimensions(payload)
            if width and height:
                self.resolution = f"{width}x{height}"
            from pcn_edge.frame_diag import FrameDiag, content_hash_jpeg

            chash = content_hash_jpeg(payload)
            frame = Frame(
                camera_id=self.config.camera_id,
                data=payload,
                captured_at=datetime.now(UTC),
                width=width,
                height=height,
                sequence=self._seq,
                content_hash=chash,
            )
            tracker = self._change_tracker
            if tracker is not None:
                tracker.observe(
                    FrameDiag(
                        sequence=frame.sequence,
                        width=width,
                        height=height,
                        content_hash=chash,
                        byte_size=len(payload),
                        captured_at_iso=frame.captured_at.isoformat(),
                    )
                )
            return frame

        self._last_error = (
            f"RTSP frame timeout after {self.config.connect_timeout_seconds:.0f}s"
            + (f" (discarded {discarded_invalid} invalid)" if discarded_invalid else "")
        )
        raise ConnectionError(self._last_error)


def validate_rtsp_url(url: str) -> tuple[bool, str]:
    if not url or not url.strip():
        return False, "RTSP URL is required"
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"rtsp", "rtsps"}:
        return False, "RTSP URL must start with rtsp:// or rtsps://"
    if not parsed.hostname:
        return False, "RTSP URL must include a host"
    return True, "ok"


def probe_rtsp_connection(rtsp_url: str, *, timeout_seconds: float = 12.0) -> dict:
    """One-shot connection test used by unit tests and local tooling."""
    ok, message = validate_rtsp_url(rtsp_url)
    if not ok:
        return {
            "ok": False,
            "message": message,
            "resolution": None,
            "fps": None,
            "first_frame_received": False,
            "redacted_url": redact_url(rtsp_url) if rtsp_url else None,
        }
    if not find_ffmpeg():
        return {
            "ok": True,
            "message": f"RTSP URL format valid ({redact_url(rtsp_url)}). FFmpeg not installed — live probe skipped.",
            "resolution": None,
            "fps": None,
            "first_frame_received": False,
            "redacted_url": redact_url(rtsp_url),
        }
    cfg = CameraConfig(
        camera_id="probe",
        rtsp_url=rtsp_url,
        connect_timeout_seconds=timeout_seconds,
        frame_interval=0.0,
        warmup_frames=2,
        min_jpeg_bytes=500,
    )
    src = RTSPFrameSource(config=cfg)
    try:
        src.open()
        frame = src.read()
        return {
            "ok": True,
            "message": f"RTSP connection successful ({redact_url(rtsp_url)})",
            "resolution": src.resolution,
            "fps": None,
            "first_frame_received": frame is not None,
            "redacted_url": redact_url(rtsp_url),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "message": str(exc),
            "resolution": None,
            "fps": None,
            "first_frame_received": False,
            "redacted_url": redact_url(rtsp_url),
        }
    finally:
        src.close()
