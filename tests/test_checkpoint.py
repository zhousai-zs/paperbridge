from paperbridge.checkpoint import cache_valid, seal_chunk


def test_corruption_invalidates_cache(tmp_path):
    for file in ('middle_json.json','markdown.md','structured_content.json'):
        (tmp_path/file).write_text('content',encoding='utf-8')
    (tmp_path/'image.png').write_bytes(b'image')
    seal_chunk(tmp_path)
    assert cache_valid(tmp_path)
    (tmp_path/'image.png').write_bytes(b'corrupted')
    assert not cache_valid(tmp_path)


def test_partial_cache_is_never_reused(tmp_path):
    (tmp_path/'markdown.md').write_text('partial')
    seal_chunk(tmp_path)
    assert not cache_valid(tmp_path)
