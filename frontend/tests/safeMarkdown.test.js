import { describe, expect, test } from 'vitest'
import { renderSafeMarkdown } from '../src/utils/safeMarkdown'

describe('renderSafeMarkdown', () => {
  test('preserves leading headings in chat messages and answers', () => {
    expect(renderSafeMarkdown('## Answer heading\n\nBody')).toContain('Answer heading</h2>')
  })

  test('omits a leading level-2 section heading already rendered by the parent', () => {
    const html = renderSafeMarkdown('## Duplicate section title\n\nBody content', { stripLeadingH2: true })

    expect(html).not.toContain('Duplicate section title')
    expect(html).toContain('<p class="md-p">Body content</p>')
  })

  test('renders report document Markdown with supported structural elements', () => {
    const html = renderSafeMarkdown([
      '# Report heading',
      '',
      'A **strong** and *emphasized* paragraph with `inline code`.',
      '',
      '> A quoted finding',
      '',
      '- First item',
      '- Second item',
      '',
      '1. First ordered item',
      '2. Second ordered item',
      '',
      '```text',
      'code block',
      '```',
      'Line one  ',
      'Line two'
    ].join('\n'))

    expect(html).toContain('<h1')
    expect(html).toContain('class="md-h2"')
    expect(html).toContain('class="md-p"')
    expect(html).toContain('<strong>strong</strong>')
    expect(html).toContain('<em>emphasized</em>')
    expect(html).toContain('<code class="inline-code">inline code</code>')
    expect(html).toContain('<pre')
    expect(html).toContain('class="code-block"')
    expect(html).toContain('<blockquote class="md-quote">')
    expect(html).toContain('class="md-quote"')
    expect(html).toContain('<ul class="md-ul">')
    expect(html).toContain('class="md-ul"')
    expect(html).toContain('<ol class="md-ol">')
    expect(html).toContain('class="md-ol"')
    expect(html).toContain('<br>')
  })

  test('removes raw HTML and active payloads from document and LLM content', () => {
    const html = renderSafeMarkdown(
      '<script>alert(1)</script><img src=x onerror=alert(1)><svg onload=alert(1)><math href="javascript:alert(1)">x</math>'
    )

    expect(html).not.toMatch(/script|img|svg|math|onerror|javascript:/i)
  })

  test('only retains safe links in chat and report content', () => {
    const html = renderSafeMarkdown(
      '[HTTPS](https://example.test) [HTTP](http://example.test) [Mail](mailto:test@example.test) [JS](javascript:alert(1)) [Data](data:text/html,boom)'
    )

    expect(html).toContain('href="https://example.test"')
    expect(html).toContain('href="http://example.test"')
    expect(html).toContain('href="mailto:test@example.test"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).not.toMatch(/href="(?:javascript|data):/i)
  })

  test.each([
    ['chat message', 'Hello **friend** <img src=x onerror=alert(1)>'],
    ['report section', '## Findings\n\n<iframe src="https://attacker.test"></iframe>'],
    ['quote', '> "Quoted" <svg><script>alert(1)</script></svg>'],
    ['placeholder', '(No response from this platform) <a href="javascript:alert(1)">x</a>'],
    ['interview answer', 'Answer with `context` <math><mi>x</mi></math>']
  ])('sanitizes %s rendering', (_name, content) => {
    const html = renderSafeMarkdown(content)

    expect(html).not.toMatch(/<\/?(?:img|iframe|script|svg|math|mi)\b|onerror|javascript:/i)
  })
})
