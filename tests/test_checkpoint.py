import json
import pytest
from PIL import Image
from paperbridge.checkpoint import cache_valid, seal_chunk, sha256
from paperbridge.integrity import IntegrityError


def make_chunk(root, image=True):
    (root / 'middle_json.json').write_text(json.dumps({'pages': [
        {'page_idx': 0, 'blocks': [{'type': 'image_body', 'image_path': 'images/x.png'}]}]}))
    (root / 'structured_content.json').write_text('{}')
    (root / 'markdown.md').write_text('![](images/x.png)')
    if image:
        (root / 'images').mkdir(exist_ok=True)
        Image.new('RGB', (8, 8), 'blue').save(root / 'images/x.png')


def test_corruption_invalidates_cache(tmp_path):
    make_chunk(tmp_path)
    seal_chunk(tmp_path)
    assert cache_valid(tmp_path)
    (tmp_path/'images/x.png').write_bytes(b'corrupted')
    assert not cache_valid(tmp_path)


def test_partial_cache_is_never_reused(tmp_path):
    (tmp_path/'markdown.md').write_text('partial')
    with pytest.raises(IntegrityError):
        seal_chunk(tmp_path)
    assert not cache_valid(tmp_path)


def test_old_manifest_cannot_hide_image_that_was_never_saved(tmp_path):
    make_chunk(tmp_path, image=False)
    files = {p.name: sha256(p) for p in tmp_path.iterdir()}
    (tmp_path / 'DONE').write_text(json.dumps({'schema_version': 1, 'files': files}))
    assert not cache_valid(tmp_path)
    with pytest.raises(IntegrityError, match='missing_image'):
        seal_chunk(tmp_path)


def test_json_only_images_and_pixel_corruption(tmp_path):
    make_chunk(tmp_path)
    (tmp_path / 'markdown.md').write_text('A JSON-only fallback image.')
    (tmp_path / 'images/x.png').write_bytes(b'not an image')
    with pytest.raises(IntegrityError, match='unreadable_image'):
        seal_chunk(tmp_path)


def test_cache_requires_exact_page_coverage(tmp_path):
    make_chunk(tmp_path)
    seal_chunk(tmp_path)
    assert cache_valid(tmp_path, expected_pages=[0])
    assert not cache_valid(tmp_path, expected_pages=[1])
