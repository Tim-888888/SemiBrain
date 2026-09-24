import io
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from PIL import Image
from semibrain_business.chunking import split_markdown
from semibrain_business.document_images import (
    bind_images,
    image_spans,
    resource_path,
    safe_image,
    slice_markdown,
)


def test_reference_images_parentheses_unicode_and_code():
    text = '![图一](../配图/工艺(旧).png "图注")\n![图二][id]\n`![假图](x)`\n```md\n![例子](x)\n```\n\n[id]: <../配图/晶圆 测试.svg>\n'
    spans = list(image_spans(text))
    assert [unquote(r['reference']) for r in spans] == ['../配图/工艺(旧).png', '../配图/晶圆 测试.svg']
    assert resource_path('知识/正文/a.md', spans[1]['reference']) == '知识/配图/晶圆 测试.svg'
    assert resource_path('a.md', '../x.png') is None
    assert resource_path('a.md', 'https://example.com/p.png') is None
    assert resource_path('a.md', '%2f%2fexample.com/x') is None


def test_heading_slices_cover_text_and_protect_image_table_code():
    image = '![中文图注](/v1/assets/12345678-1234-1234-1234-123456789012/content)'
    text = '# 工艺\n\n## 刻蚀\n\n' + '知识说明。' * 410 + '\n\n' + image + '\n\n|条件|值|\n|---|---|\n|温度|25|\n\n' + '```python\nx=1\n```\n' + '后续说明。' * 650
    rows = split_markdown(text)
    assert ''.join(r['text'] for r in rows) == text
    assert len([r for r in rows if image in r['text']]) == 1
    assert all(text[r['location']['character_start']:r['location']['character_end']] == r['text'] for r in rows)
    assert rows[-1]['context_header'] == '工艺 > 刻蚀'
    assert any('|条件|值|\n|---|---|\n|温度|25|' in r['text'] for r in rows)


def test_binding_same_basename_and_missing_resources():
    documents = {'path':'book/chapter.md','_id':'doc','owner_id':'owner'}
    assets = {'a':{'_id':'a','ref':{'content_hash':'a'*64,'media_type':'image/png'}},
              'b':{'_id':'b','ref':{'content_hash':'b'*64,'media_type':'image/png'}}}
    parsed = SimpleNamespace(markdown='![A](one/图.png)\n![B](two/图.png)\n![缺图](other.png)',
                             images={}, image_refs=[], blocks=[], quality_findings=[], status='staged')
    job={'version':'v','image_attachments':[{'path':'book/one/图.png','asset_id':'a'},{'path':'book/two/图.png','asset_id':'b'}]}
    ids = bind_images(parsed,documents,job,None,None,SimpleNamespace(find_one=lambda q:assets[q['_id']]))
    assert ids == ['a','b'] and parsed.status == 'needs_attention'
    for ref in parsed.image_refs:
        assert ref['url'] in parsed.markdown[ref['start']:ref['end']]
    a,b,refs=slice_markdown(parsed.markdown,0,parsed.image_refs[0]['start']+2,parsed.image_refs)
    assert a == 0 and b == parsed.image_refs[0]['end'] and len(refs)==1


@pytest.mark.parametrize('payload',[
    '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://host/a"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><use href="file:///a"/></svg>',
    '<!DOCTYPE svg [<!ENTITY x "hello">]><svg>&x;</svg>',
])
def test_svg_active_content_rejected(payload):
    with pytest.raises(ValueError):
        safe_image(payload.encode(),'x.svg')


def test_valid_image_bytes_and_local_svg_shapes():
    raw=io.BytesIO()
    Image.new('RGB',(20,20)).save(raw,format='PNG')
    assert safe_image(raw.getvalue(),'中文.png')[1]=='image/png'
    svg=b'<svg xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="g"/></defs><rect fill="url(#g)" width="20" height="20"/></svg>'
    assert safe_image(svg,'x.svg')[1]=='image/svg+xml'
