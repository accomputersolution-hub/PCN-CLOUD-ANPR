from __future__ import annotations

"""RTSP probe via FFmpeg. Never logs credentials."""

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger
from app.services.rtsp_url import redact_rtsp_url, validate_rtsp_url_shape

logger = get_logger(__name__)


@dataclass
class RtspProbeResult:
    ok: bool
    message: str
    probe: str = "ffmpeg"
    resolution: str | None = None
    fps: float | None = None
    first_frame_received: bool = False
    redacted_url: str | None = None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_rtsp(
    rtsp_url: str | None,
    *,
    timeout_seconds: float = 12.0,
    transport: str = "tcp",
) -> RtspProbeResult:
    if not rtsp_url:
        return RtspProbeResult(ok=False, message="No RTSP URL configured", probe="url_validation")
    ok, message = validate_rtsp_url_shape(rtsp_url)
    if not ok:
        return RtspProbeResult(ok=False, message=message, probe="url_validation", redacted_url=redact_rtsp_url(rtsp_url))

    redacted = redact_rtsp_url(rtsp_url)
    if not ffmpeg_available():
        # Still validate shape so UI can proceed in environments without ffmpeg.
        return RtspProbeResult(
            ok=True,
            message=f"RTSP URL format is valid ({redacted}). FFmpeg not installed on API host — live probe skipped.",
            probe="url_validation",
            redacted_url=redacted,
            first_frame_received=False,
        )

    with tempfile.TemporaryDirectory(prefix="pcn-rtsp-") as tmp:
        out = Path(tmp) / "frame.jpg"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            transport,
            "-i",
            rtsp_url,
            "-frames:v",
            "1",
            "-y",
            str(out),
        ]
        logger.info("rtsp.probe.start", url=redacted, transport=transport)
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.info("rtsp.probe.timeout", url=redacted)
            return RtspProbeResult(
                ok=False,
                message=f"RTSP probe timed out after {timeout_seconds:.0f}s for {redacted}",
                probe="ffmpeg",
                redacted_url=redacted,
            )
        except FileNotFoundError:
            return RtspProbeResult(
                ok=False,
                message="FFmpeg executable not found",
                probe="ffmpeg",
                redacted_url=redacted,
            )

        if completed.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            err = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
            # Scrub any accidental credential echo from ffmpeg stderr.
            safe_err = err.replace(rtsp_url, redacted) if err else "Unable to pull first frame"
            logger.info("rtsp.probe.failed", url=redacted, code=completed.returncode)
            return RtspProbeResult(
                ok=False,
                message=f"RTSP connection failed for {redacted}: {safe_err[:300]}",
                probe="ffmpeg",
                redacted_url=redacted,
                first_frame_received=False,
            )

        resolution = _probe_resolution(out)
        logger.info("rtsp.probe.ok", url=redacted, resolution=resolution)
        return RtspProbeResult(
            ok=True,
            message=f"RTSP connection successful for {redacted}",
            probe="ffmpeg",
            resolution=resolution,
            fps=None,
            first_frame_received=True,
            redacted_url=redacted,
        )


def _probe_resolution(path: Path) -> str | None:
    try:
        from PIL import Image  # optional

        with Image.open(path) as img:
            w, h = img.size
            return f"{w}x{h}"
    except Exception:
        return None
