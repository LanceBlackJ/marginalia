import { describe, expect, it, vi } from 'vitest'

vi.mock('electron', () => ({ app: { getPath: () => '/tmp' }, BrowserWindow: class {}, Menu: {}, clipboard: {}, dialog: {}, session: {}, shell: {} }))

import { isHttpUrl, looksLikePdf, safePdfName } from './paperBrowser'

describe('论文下载浏览器的纯函数', () => {
  it('只认 http/https 网址', () => {
    expect(isHttpUrl('https://doi.org/10.1/x')).toBe(true)
    expect(isHttpUrl('file:///C:/secret.txt')).toBe(false)
    expect(isHttpUrl('javascript:alert(1)')).toBe(false)
    expect(isHttpUrl('not a url')).toBe(false)
  })
  it('下载文件名去掉路径和奇怪字符，且以 .pdf 结尾', () => {
    expect(safePdfName('../../evil.exe')).toBe('evil.exe.pdf')
    expect(safePdfName('Attention Is All You Need.pdf')).toBe('Attention Is All You Need.pdf')
    expect(safePdfName('a<b>:c?.pdf')).toBe('a_b_c_.pdf')
    expect(safePdfName('')).toBe('paper.pdf')
  })
  it('按类型或扩展名判断是不是 PDF', () => {
    const item = (mime: string, name: string) => ({ getMimeType: () => mime, getFilename: () => name })
    expect(looksLikePdf(item('application/pdf', 'x'))).toBe(true)
    expect(looksLikePdf(item('application/octet-stream', 'paper.PDF'))).toBe(true)
    expect(looksLikePdf(item('application/zip', 'supp.zip'))).toBe(false)
  })
})
