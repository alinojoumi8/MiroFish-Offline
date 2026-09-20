import DOMPurify from 'dompurify'
import { marked } from 'marked'

const ALLOWED_TAGS = [
  'a', 'blockquote', 'br', 'code', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'hr', 'li', 'ol', 'p', 'pre', 'strong', 'ul'
]

const ALLOWED_ATTR = ['class', 'href', 'rel', 'target', 'title']
const SAFE_LINK_PROTOCOL = /^(?:https?|mailto):/i
const STYLE_CLASS_BY_TAG = {
  BLOCKQUOTE: 'md-quote',
  H1: 'md-h2',
  H2: 'md-h3',
  H3: 'md-h4',
  H4: 'md-h5',
  H5: 'md-h5',
  H6: 'md-h5',
  HR: 'md-hr',
  OL: 'md-ol',
  P: 'md-p',
  PRE: 'code-block',
  UL: 'md-ul'
}

const renderer = new marked.Renderer()

renderer.html = () => ''
renderer.link = function link({ href, title, tokens }) {
  const label = this.parser.parseInline(tokens)
  const titleAttribute = title ? ` title="${title}"` : ''
  return `<a href="${href}"${titleAttribute} target="_blank" rel="noopener noreferrer">${label}</a>`
}

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A' && SAFE_LINK_PROTOCOL.test(node.getAttribute('href') || '')) {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

DOMPurify.addHook('afterSanitizeElements', (node) => {
  const styleClass = STYLE_CLASS_BY_TAG[node.tagName]
  if (styleClass) node.classList.add(styleClass)

  if (node.tagName === 'LI') {
    node.classList.add(node.parentNode?.tagName === 'OL' ? 'md-oli' : 'md-li')
  }

  if (node.tagName === 'CODE' && node.parentNode?.tagName !== 'PRE') {
    node.classList.add('inline-code')
  }
})

export function renderSafeMarkdown(content, { stripLeadingH2 = false } = {}) {
  if (content === null || content === undefined || content === '') return ''

  // Report section titles are rendered by their parent components.
  const markdown = stripLeadingH2
    ? String(content).replace(/^##\s+.+(?:\r?\n)+/, '')
    : String(content)
  const markdownHtml = marked.parse(markdown, {
    async: false,
    breaks: true,
    gfm: true,
    renderer
  })

  return DOMPurify.sanitize(markdownHtml, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_ARIA_ATTR: false,
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: SAFE_LINK_PROTOCOL,
    FORBID_ATTR: ['style'],
    FORBID_TAGS: ['math', 'script', 'svg']
  })
}
