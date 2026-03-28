import io

from fastapi.testclient import TestClient
from PIL import Image

from app import PROJECT_URL, app

client = TestClient(app)


def generate_jpeg() -> bytes:
    img = Image.new("RGB", (10, 10), color="red")
    output = io.BytesIO()
    img.save(output, format="JPEG")
    return output.getvalue()


def test_process_and_download_jpeg():
    img_bytes = generate_jpeg()

    response = client.post(
        "/api/process",
        files={"file": ("test.jpg", img_bytes, "image/jpeg")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "download_url" in payload
    assert payload["original_filename"] == "test.jpg"

    download_path = payload["download_url"].replace("http://localhost:8000", "")
    dl_response = client.get(download_path)
    assert dl_response.status_code == 200


def test_reject_invalid_content_type():
    response = client.post(
        "/api/process",
        files={"file": ("bad.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_home_page_contains_form():
    response = client.get("/")
    assert response.status_code == 200
    assert "<form" in response.text
    assert PROJECT_URL not in response.text
