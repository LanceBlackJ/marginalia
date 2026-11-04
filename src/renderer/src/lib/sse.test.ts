import { describe, expect, it } from 'vitest'
import { SSEParser, readSSE, type SSEMessage } from './sse'

function parseAll(chunks: string[]): SSEMessage[] {
  const p = new SSEParser()
  const out: SSEMessage[] = []
  for (const c of chunks) out.push(...p.feed(c))
  out.push(...p.flush())
  return out
}

describe('SSEParser', () => {
  it('解析单个事件', () => {
    expect(parseAll(['event: delta\ndata: {"text":"你好"}\n\n'])).toEqual([{ event: 'delta', data: '{"text":"你好"}' }])
  })

  it('没有 event 字段时为 message', () => {
    expect(parseAll(['data: hi\n\n'])).toEqual([{ event: 'message', data: 'hi' }])
  })

  it('处理 CRLF 换行', () => {
    expect(parseAll(['event: meta\r\ndata: {"a":1}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n'])).toEqual([
      { event: 'meta', data: '{"a":1}' },
      { event: 'done', data: '{}' }
    ])
  })

  it('多行 data 用换行连接', () => {
    expect(parseAll(['event: x\ndata: line1\ndata: line2\n\n'])).toEqual([{ event: 'x', data: 'line1\nline2' }])
  })

  it('事件被任意切块：每个字符一块', () => {
    const text = 'event: delta\ndata: {"text":"ab"}\n\nevent: done\ndata: {"ok":true}\n\n'
    expect(parseAll(text.split(''))).toEqual([
      { event: 'delta', data: '{"text":"ab"}' },
      { event: 'done', data: '{"ok":true}' }
    ])
  })

  it('CRLF 被切在 \\r 与 \\n 之间', () => {
    expect(parseAll(['event: a\r', '\ndata: 1\r', '\n\r', '\n'])).toEqual([{ event: 'a', data: '1' }])
  })

  it('忽略注释行与未知字段，data 冒号后只去掉一个空格', () => {
    expect(parseAll([': keep-alive\nid: 7\nevent: t\ndata:  two spaces\n\n'])).toEqual([{ event: 't', data: ' two spaces' }])
  })

  it('流结束时派发未以空行结束的事件', () => {
    expect(parseAll(['event: done\ndata: {"x":1}'])).toEqual([{ event: 'done', data: '{"x":1}' }])
  })

  it('data 中可以包含冒号', () => {
    expect(parseAll(['data: {"url":"http://a:b"}\n\n'])).toEqual([{ event: 'message', data: '{"url":"http://a:b"}' }])
  })

  it('空 data 事件被保留', () => {
    expect(parseAll(['event: ping\ndata:\n\n'])).toEqual([{ event: 'ping', data: '' }])
  })
})

describe('readSSE', () => {
  it('从 ReadableStream 解码多字节字符且不丢失被切开的 UTF-8', async () => {
    const bytes = new TextEncoder().encode('event: delta\ndata: {"text":"你好，世界"}\n\n')
    const cut = 22 // 落在“你”的中间
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, cut))
        controller.enqueue(bytes.slice(cut))
        controller.close()
      }
    })
    const got: SSEMessage[] = []
    await readSSE(stream, (m) => got.push(m))
    expect(got).toHaveLength(1)
    expect(JSON.parse(got[0].data)).toEqual({ text: '你好，世界' })
  })
})
