from httpx import AsyncClient

from tests.conftest import login


async def test_anpr_test_image_requires_auth(client: AsyncClient) -> None:
    res = await client.post("/api/v1/anpr/test-image", files={"file": ("x.jpg", b"abc", "image/jpeg")})
    assert res.status_code in {401, 403}


async def test_anpr_test_image_dev(client: AsyncClient, world: dict, tmp_path_factory) -> None:
    import numpy as np

    try:
        import cv2
    except ImportError:
        return

    token = await login(client, "orgadmin@pcncloud.in")
    img = np.zeros((64, 96, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    res = await client.post(
        "/api/v1/anpr/test-image",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("frame.jpg", buf.tobytes(), "image/jpeg")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["development_only"] is True
    assert "plate_detected" in body
    assert "vehicles" in body
    # Must not create events — response only
    assert "event_id" not in body
