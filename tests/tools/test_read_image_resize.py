import base64
import io

from PIL import Image

from vtx.tools._read_image import MAX_DIMENSION, read_and_process_image


def _write_image(path, size: tuple[int, int], color: str = "red") -> None:
    img = Image.new("RGB", size, color=color)
    img.save(path)


def _decoded_size(base64_data: str) -> tuple[int, int]:
    data = base64.b64decode(base64_data)
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def test_read_image_downsizes_large_images(tmp_path):
    image_path = tmp_path / "large.png"
    _write_image(image_path, (2520, 842))

    base64_data, mime_type, resize_note = read_and_process_image(str(image_path))

    assert mime_type == "image/png"
    assert _decoded_size(base64_data) == (MAX_DIMENSION, 668)
    assert resize_note == f"[{MAX_DIMENSION}x668, resized from 2520x842]"


def test_read_image_keeps_small_images(tmp_path):
    image_path = tmp_path / "small.png"
    _write_image(image_path, (100, 100))

    base64_data, mime_type, resize_note = read_and_process_image(str(image_path))

    assert mime_type == "image/png"
    assert _decoded_size(base64_data) == (100, 100)
    assert resize_note == "[100x100]"
