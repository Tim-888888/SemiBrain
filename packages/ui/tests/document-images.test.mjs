import test from 'node:test'
import assert from 'node:assert/strict'
import { renderMarkdown } from '../src/markdown.mjs'
import { documentBundle, resolveImagePath } from '../src/knowledge-upload.mjs'

const url='/v1/assets/12345678-1234-1234-1234-123456789012/content'
test('only registered same-origin resources render in ordinary markdown', () => {
  const source=`正文\n\n![图注](${url})\n\n后续解释`
  assert.match(renderMarkdown(source,[{url,alt:'图注'}]),/<img src=/)
  assert.doesNotMatch(renderMarkdown(source),/<img/)
  assert.doesNotMatch(renderMarkdown('![图](https://tracking.example/x)',[{url:'https://tracking.example/x'}]),/<img/)
  assert.doesNotMatch(renderMarkdown('<img src=x onerror=alert(1)>'),/<img/)
  assert.match(renderMarkdown(source,[{url,display_url:url+'?preview_version=12345678-1234-1234-1234-123456789012'}]),/preview_version=/)
})
test('folder upload resolves Chinese relative images and reference definitions',async () => {
  const doc={name:'文档.md',webkitRelativePath:'目录/章节/文档.md',size:200,text:async()=> '![工艺图][p]\n\n[p]: <../配图/晶圆 图.svg>'}
  const img={name:'晶圆 图.svg',webkitRelativePath:'目录/配图/晶圆 图.svg',size:100}
  const images=await documentBundle(doc,[doc,img],doc.webkitRelativePath)
  assert.equal(images[0].path,img.webkitRelativePath)
  await assert.rejects(documentBundle(doc,[doc],doc.webkitRelativePath),/图片未找到/)
  assert.equal(resolveImagePath('a.md','../x.png'),null)
  assert.equal(resolveImagePath('a.md','https://example.com/x.png'),null)
})
