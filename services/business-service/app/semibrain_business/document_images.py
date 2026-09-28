"""Document-scoped Markdown resources; adapted concepts are recorded in THIRD_PARTY_NOTICES."""

import base64
import io
import posixpath
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from PIL import Image

IMAGE_LIMIT = 50
BUNDLE_LIMIT = 32 * 1024**2
SVG_TAGS = set('svg g defs symbol use path rect circle ellipse line polyline polygon text tspan textPath title desc style clipPath mask linearGradient radialGradient stop pattern marker filter feGaussianBlur feOffset feMerge feMergeNode feColorMatrix feDropShadow'.split())


def unsafe_style(value):
    value = re.sub(r'/\*.*?\*/', '', value, flags=re.S)
    return ('\\' in value or '@' in value or '<' in value
            or bool(re.search(r'(?:javascript|data|https?|file):|expression\s*\(', value, re.I))
            or any(not target.strip(' \t\r\n\'"').startswith('#')
                   for target in re.findall(r'url\(([^)]*)\)', value, re.I)))


def safe_image(raw, name):
    """Validate raster bytes or produce an inert SVG; never fetch remote image sources."""
    if not raw or len(raw) > 16 * 1024**2:
        raise ValueError('DOCUMENT_IMAGE_SIZE_INVALID')
    if name.lower().endswith('.svg'):
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('UNSAFE_SVG')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ValueError('IMAGE_FORMAT_INVALID') from exc
        if root.tag.split('}')[-1] != 'svg':
            raise ValueError('IMAGE_FORMAT_INVALID')
        for element in root.iter():
            if element.tag.split('}')[-1] not in SVG_TAGS:
                raise ValueError('UNSAFE_SVG')
            if element.tag.split('}')[-1] == 'style' and unsafe_style(element.text or ''):
                raise ValueError('UNSAFE_SVG')
            for key, value in list(element.attrib.items()):
                key = key.split('}')[-1].lower()
                if (key.startswith('on') or key in {'src', 'base'}
                        or (key == 'href' and not value.startswith('#'))
                        or re.search(r'(?:javascript|data|https?|file):|@import|expression\s*\(', value, re.I)
                        or (key == 'style' and unsafe_style(value))
                        or any(not target.strip(' \t\r\n\'"').startswith('#') for target in re.findall(r'url\(([^)]*)\)', value, re.I))):
                    raise ValueError('UNSAFE_SVG')
        ET.register_namespace('', 'http://www.w3.org/2000/svg')
        return ET.tostring(root, encoding='utf-8'), 'image/svg+xml'
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP', 'GIF'} or image.width * image.height > 40_000_000:
                raise ValueError('IMAGE_FORMAT_INVALID')
            media = Image.MIME[image.format]
            image.verify()
    except Exception as exc:
        raise ValueError('IMAGE_FORMAT_INVALID') from exc
    return raw, media


def resource_path(document_path, reference):
    decoded = unquote(reference).replace('\\', '/')
    parts = urlsplit(decoded)
    if parts.scheme or parts.netloc or decoded.startswith('/') or '\x00' in decoded:
        return None
    value = posixpath.normpath(posixpath.join(posixpath.dirname(document_path), parts.path))
    if value == '..' or value.startswith('../') or ':' in value or len(value) > 500:
        return None
    return value


def image_spans(text):
    """Balanced destinations and document-wide reference definitions; code is not an image."""
    env = {}
    tokens = MarkdownIt('commonmark').parse(text, env)
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    excluded = [(offsets[t.map[0]], offsets[t.map[1]]) for t in tokens
                if t.type in {'fence', 'code_block'} and t.map]
    excluded += [(m.start(), m.end()) for m in re.finditer(r'(`+)[^\n]*?\1', text)]
    for match in re.finditer(r'!\[((?:\\.|[^\]\\\n]){0,600})\]', text):
        if any(a <= match.start() < b for a, b in excluded):
            continue
        start, end, alt = match.start(), match.end(), match.group(1)
        reference = None
        if text[end:end+1] == '(':
            depth, cursor, escaped, angled = 1, end+1, False, False
            while cursor < min(len(text), end+8192):
                char = text[cursor]
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '<':
                    angled = True
                elif char == '>':
                    angled = False
                elif not angled and char == '(':
                    depth += 1
                elif not angled and char == ')':
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1
            if depth:
                continue
            destination = text[end+1:cursor].strip()
            if destination.startswith('<') and '>' in destination:
                reference = destination[1:destination.index('>')]
            else:
                reference = re.split(r'\s+["\']', destination, maxsplit=1)[0]
            reference = re.sub(r'\\([()\[\] ])', r'\1', reference)
            end = cursor + 1
        else:
            ref = re.match(r'\[([^\]\n]*)\]', text[end:])
            label = (ref.group(1) or alt) if ref else alt
            definition = env.get('references', {}).get(' '.join(label.split()).upper())
            if definition:
                reference = definition['href']
                if ref:
                    end += ref.end()
        if reference is not None:
            yield {'start': start, 'end': end, 'alt': alt, 'reference': reference}


def bind_images(parsed, document, job, store, read, assets):
    """Resolve by full normalized path, never basename. Fail visibly if any resource is absent."""
    uploaded = {r['path']: r['asset_id'] for r in job.get('image_attachments', [])}
    generated = parsed.images
    cache, refs, pieces, cursor, length = {}, [], [], 0, 0
    for span in image_spans(parsed.markdown):
        original = span['reference']
        key = resource_path(document['path'], original)
        image = cache.get(original)
        if image is None:
            asset_id = uploaded.get(key)
            if asset_id:
                image = assets.find_one({'_id': asset_id, 'document_id': document['_id']})
            else:
                encoded = generated.get(original)
                if encoded:
                    raw = base64.b64decode(encoded.split(',', 1)[-1] if encoded.startswith('data:') else encoded)
                    safe, media = safe_image(raw, original)
                    image = store(safe, media, document['owner_id'], posixpath.basename(original), document_id=document['_id'])
            if image:
                cache[original] = image
        prefix = parsed.markdown[cursor:span['start']]
        pieces.append(prefix)
        length += len(prefix)
        alt = span['alt'].replace('[', '\\[').replace(']', '\\]')
        if image:
            url = '/v1/assets/' + image['_id'] + '/content'
            replacement = '![' + alt + '](' + url + ')'
            refs.append({'asset_id': image['_id'], 'url': url, 'alt': span['alt'],
                         'original_ref': original, 'document_id': document['_id'], 'version': job['version'],
                         'content_hash': image['ref']['content_hash'], 'media_type': image['ref']['media_type'],
                         'start': length, 'end': length + len(replacement)})
        else:
            replacement = '[图片未随文档提供：' + alt + ']'
            parsed.quality_findings.append('IMAGE_RESOURCE_MISSING: ' + original[:200])
            parsed.status = 'needs_attention'
        pieces.append(replacement)
        length += len(replacement)
        cursor = span['end']
    pieces.append(parsed.markdown[cursor:])
    parsed.markdown = ''.join(pieces)
    parsed.image_refs = refs
    if refs or cache:
        # Upstream block text predates resource resolution; use canonical Markdown locations.
        parsed.blocks = []
    if len(refs) > IMAGE_LIMIT:
        raise ValueError('DOCUMENT_IMAGE_COUNT_INVALID')
    return list(dict.fromkeys(ref['asset_id'] for ref in refs))


def slice_markdown(content, start, end, refs):
    for ref in refs:
        if ref['start'] < start < ref['end']:
            start = ref['start']
        if ref['start'] < end < ref['end']:
            end = ref['end']
    return start, min(end, len(content)), [r for r in refs if r['start'] < end and r['end'] > start]
