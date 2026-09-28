import assert from 'node:assert/strict'
import test from 'node:test'
import { clipboardImages, imageError, MAX_IMAGE_BYTES, ScrollFollow } from '../src/composer.mjs'

test('image paste extracts all image files once and leaves text-only paste alone', () => {
  const png = { type: 'image/png' }, webp = { type: 'image/webp' }
  const item = file => ({ kind: 'file', type: file.type, getAsFile: () => file })
  assert.deepEqual(clipboardImages({ items: [item(png), item(webp)], files: [png, webp] }), [png, webp])
  assert.deepEqual(clipboardImages({ items: [{ kind: 'string', type: 'text/plain' }], files: [] }), [])
  assert.deepEqual(clipboardImages({ items: [], files: [png] }), [png])
  assert.deepEqual(clipboardImages(null), [])
})

test('picker and pasted images share format and byte limits', () => {
  for (const type of ['image/png', 'image/jpeg', 'image/webp']) assert.equal(imageError({ type, size: MAX_IMAGE_BYTES }), '')
  const empty = imageError({ type: 'image/png', size: 0 })
  const oversized = imageError({ type: 'image/png', size: MAX_IMAGE_BYTES + 1 })
  assert.match(empty, /为空/)
  assert.doesNotMatch(empty, /超过/)
  assert.match(oversized, /超过 3 MB/)
  assert.doesNotMatch(oversized, /为空/)
  for (const file of [{ type: 'image/svg+xml', size: 20 }, { type: '', size: 20 }]) assert.ok(imageError(file))
})

function scrollFixture() {
  const area = { scrollTop: 0, clientHeight: 400, scrollHeight: 1200 }
  const follow = new ScrollFollow(() => area)
  follow.sync()
  return { area, follow }
}

test('streaming content follows at bottom but manual upward movement stops it even within tolerance', () => {
  const { area, follow } = scrollFixture()
  area.scrollHeight += 80; follow.sync(); assert.equal(area.scrollTop, 880)
  area.scrollTop -= 12; follow.scrolled()
  area.scrollHeight += 100; follow.sync(); follow.scrolled()
  assert.equal(area.scrollTop, 868); assert.equal(follow.following, false)
})

test('upward intent before a queued render cancels following; latest button resumes', async () => {
  const { area, follow } = scrollFixture()
  const queued = Promise.resolve().then(() => follow.sync())
  follow.pause(); area.scrollHeight += 200
  await queued; assert.equal(area.scrollTop, 800)
  follow.resume(); follow.sync(); assert.equal(area.scrollTop, 1000)
})

test('scrolling down near the live edge resumes following and delayed image resize follows', () => {
  const { area, follow } = scrollFixture()
  area.scrollTop = 300; follow.scrolled(); assert.equal(follow.following, false)
  area.scrollHeight += 100; area.scrollTop = 880; follow.scrolled()
  assert.equal(follow.following, true)
  area.scrollHeight += 100; follow.sync(); assert.equal(area.scrollTop, 1000)
})

test('paused reader is not moved by terminal content replacement and new chat resets intent', () => {
  const { area, follow } = scrollFixture()
  area.scrollTop = 200; follow.scrolled()
  area.scrollHeight += 500; follow.sync(); assert.equal(area.scrollTop, 200)
  follow.reset(); follow.sync(); assert.equal(area.scrollTop, 1300)
})
