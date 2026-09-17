from app.services.rtsp_url import build_rtsp_url, redact_rtsp_url, validate_rtsp_url_shape


def test_validate_rtsp_url_shape() -> None:
    ok, _ = validate_rtsp_url_shape("rtsp://192.168.1.64/stream")
    assert ok
    bad, msg = validate_rtsp_url_shape("http://x")
    assert not bad
    assert "rtsp" in msg.lower()


def test_redact_strips_password() -> None:
    url = "rtsp://admin:s3cret@192.168.1.64:554/cam/realmonitor?channel=1&subtype=0"
    redacted = redact_rtsp_url(url)
    assert "s3cret" not in redacted
    assert "***" in redacted
    assert "192.168.1.64" in redacted


def test_build_rtsp_url_merges_credentials() -> None:
    built = build_rtsp_url("rtsp://192.168.1.64:554/stream", "admin", "p@ss")
    assert built.startswith("rtsp://admin:")
    assert "192.168.1.64:554/stream" in built
    assert "p@ss" in built or "%40" in built  # @ may be percent-encoded
