import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import go from 'highlight.js/lib/languages/go'
import ini from 'highlight.js/lib/languages/ini'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import makefile from 'highlight.js/lib/languages/makefile'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

const LANGS: Record<string, Parameters<typeof hljs.registerLanguage>[1]> = {
  bash, c, cpp, dockerfile, go, ini, java, javascript, json, makefile, markdown, python, rust, sql, typescript, xml, yaml
}
for (const [name, def] of Object.entries(LANGS)) hljs.registerLanguage(name, def)

const EXT: Record<string, string> = {
  py: 'python', pyx: 'python', pxd: 'python', pxi: 'python', pyi: 'python',
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  java: 'java', c: 'c', h: 'cpp', cc: 'cpp', cpp: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp', cu: 'cpp', cuh: 'cpp',
  go: 'go', rs: 'rust', sh: 'bash', bash: 'bash', zsh: 'bash',
  yml: 'yaml', yaml: 'yaml', json: 'json', toml: 'ini', ini: 'ini', cfg: 'ini', conf: 'ini',
  md: 'markdown', markdown: 'markdown', xml: 'xml', html: 'xml', htm: 'xml', sql: 'sql', mk: 'makefile'
}
const NAMES: Record<string, string> = { dockerfile: 'dockerfile', makefile: 'makefile', 'cmakelists.txt': 'makefile' }

/** 按文件名/扩展名选择高亮语言；认不出来就返回 null（按纯文本显示）。 */
export function languageForPath(path: string): string | null {
  const name = path.split('/').pop()!.toLowerCase()
  if (NAMES[name]) return NAMES[name]
  const dot = name.lastIndexOf('.')
  if (dot < 0) return null
  return EXT[name.slice(dot + 1)] ?? null
}

export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

/**
 * 把高亮后的 HTML 按行拆开；跨行的 <span> 在行尾关闭、下一行重新打开，
 * 这样每一行都是独立且标签平衡的 HTML，可以单独渲染。
 */
export function splitHtmlLines(html: string): string[] {
  const lines: string[] = []
  const open: string[] = []
  let cur = ''
  for (const part of html.split(/(<span[^>]*>|<\/span>|\n)/)) {
    if (part === '') continue
    if (part === '\n') {
      lines.push(cur + '</span>'.repeat(open.length))
      cur = open.join('')
    } else if (part === '</span>') {
      open.pop()
      cur += part
    } else if (part.startsWith('<span')) {
      open.push(part)
      cur += part
    } else cur += part
  }
  lines.push(cur + '</span>'.repeat(open.length))
  return lines
}

export const MAX_HIGHLIGHT_CHARS = 250_000

/** 返回每一行的 HTML（已转义）。过大的文件不做语法高亮，避免卡顿。 */
export function highlightLines(code: string, language: string | null): string[] {
  const text = code.endsWith('\n') ? code.slice(0, -1) : code
  if (!language || text.length > MAX_HIGHLIGHT_CHARS || !hljs.getLanguage(language)) return text.split('\n').map(escapeHtml)
  try {
    return splitHtmlLines(hljs.highlight(text, { language, ignoreIllegals: true }).value)
  } catch {
    return text.split('\n').map(escapeHtml)
  }
}
