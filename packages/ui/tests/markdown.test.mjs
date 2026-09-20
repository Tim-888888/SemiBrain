import assert from 'node:assert/strict'
import test from 'node:test'
import { renderMarkdown } from '../src/markdown.mjs'

test('multiline display formulas cannot become setext headings', () => {
  for (const source of [String.raw`\[
X
=
\frac{a-b}{s}
\]`, '$$\nx\n=\ny+1\n$$']) {
    const html = renderMarkdown(source)
    assert.match(html, /class="katex/)
    assert.doesNotMatch(html, /<h[1-6]/)
  }
})

test('inline math, tables and code retain their own semantics', () => {
  assert.match(renderMarkdown(String.raw`Use \(x^2\) or $y_1$ [1].`), /katex/)
  assert.match(renderMarkdown('| Quantity | Value |\n|---|---|\n| $x$ | 3 |'), /<table>/)
  const literal = renderMarkdown('```text\n\\[x=1\\]\n```')
  assert.match(literal, /<pre><code/)
  assert.doesNotMatch(literal, /class="katex/)
  assert.equal(renderMarkdown('Price $10 and $20.'), '<p>Price $10 and $20.</p>\n')
})

test('untrusted formulas cannot activate links or external images', () => {
  for (const source of [
    String.raw`\(\href{javascript:alert(1)}{click}\)`,
    String.raw`\(\includegraphics{https://example.com/secret}\)`,
    String.raw`\(\htmlData{secret=one}{x}\)`,
    String.raw`\(\badcommand{<img src=x onerror=alert(1)>}\)`,
  ]) {
    const html = renderMarkdown(source)
    assert.doesNotMatch(html, /<a\s|<img\s|<script|data-secret=/i)
  }
  assert.doesNotMatch(renderMarkdown('<script>alert(1)</script>'), /<script>/)
})

test('incomplete formulas remain readable and malformed macros are bounded', () => {
  assert.match(renderMarkdown(String.raw`before \[x+y`), /before/)
  assert.match(renderMarkdown(String.raw`\(\def\a{\a}\a\)`), /katex-error/)
  assert.doesNotMatch(renderMarkdown('\\(' + 'x'.repeat(8001) + '\\)'), /class="katex/)
})
