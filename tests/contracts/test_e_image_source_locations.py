from semibrain_business.chunk_tree import build_chunk_tree
from semibrain_business.document_images import source_spans
from semibrain_business.knowledge_revisions import revise
from semibrain_business.parsing import Block, ParseResult
from semibrain_common.runtime import digest


def test_resolved_images_preserve_native_locators_and_repeated_text_order():
    markdown = '# Deck\n\nSame text\n\n![diagram](/v1/assets/abc/content)\n\nSame text'
    blocks = [Block(kind='slide_text', text='Same text', location={'slide': 1}),
              Block(kind='image', text='![diagram](image.png)', location={'slide': 1}),
              Block(kind='slide_text', text='Same text', location={'slide': 2})]
    spans = source_spans(markdown, blocks, [{'original_ref': 'image.png', 'url': '/v1/assets/abc/content'}])
    assert [s['location']['slide'] for s in spans] == [1, 1, 2]
    assert spans[2]['start'] == markdown.rindex('Same text')
    parsed = ParseResult(status='staged', source_hash='hash', markdown=markdown, source_spans=spans)
    chunks, parents = build_chunk_tree(parsed, 'doc', 'v2', 'embedding')
    assert {loc['slide'] for chunk in chunks for loc in chunk['location']['source_locations']} == {1, 2}
    assert parents[0]['location']['source_locations']


def test_native_table_mapping_does_not_guess_unmatched_blocks():
    markdown = '| Stage | Count |\n| --- | --- |\n| FT | 53 |\n'
    blocks = [Block(kind='table_row', text='["Stage", "Count"]', location={'body_element': 2, 'row': 0}),
              Block(kind='table_row', text='["FT", "53"]', location={'body_element': 2, 'row': 1}),
              Block(kind='paragraph', text='absent original', location={'body_element': 3})]
    spans = source_spans(markdown, blocks, [])
    assert len(spans) == 2
    assert markdown[spans[1]['start']:spans[1]['end']].strip() == '| FT | 53 |'


def test_revision_shifts_later_provenance_and_marks_changed_source():
    text = 'First\nSecond'
    parsed = ParseResult(status='staged', source_hash='hash', markdown=text, source_spans=[
        {'start': 0, 'end': 5, 'block_index': 0, 'location': {'slide': 1}},
        {'start': 6, 'end': 12, 'block_index': 1, 'location': {'slide': 2}}])
    chunk = {'_id': 'chunk', 'version': 'v1', 'text': 'First', 'content_hash': digest('First'),
             'location': {'coordinate_system': 'document_markdown', 'character_start': 0, 'character_end': 5}}
    updated = revise(parsed, chunk, 'First revised', 'v2')
    assert updated.source_spans[0]['location']['source'] == 'manual_revision'
    assert updated.markdown[updated.source_spans[1]['start']:updated.source_spans[1]['end']] == 'Second'


def test_record_line_numbers_do_not_imply_whole_document_markdown_offsets():
    parsed = ParseResult(status='staged', source_hash='hash', markdown='| A | B |', blocks=[
        Block(kind='table_row', text='["A", "B"]', location={'record': 1, 'line_start': 1, 'line_end': 1})])
    chunks, _ = build_chunk_tree(parsed, 'doc', 'version', 'embedding')
    assert chunks[0]['location']['record'] == 1
    assert chunks[0]['location']['coordinate_system'] == 'parser_block'
