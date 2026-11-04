/**
 * 极简 SSE 解析器。
 * 处理：分块到达（事件/行/CRLF 可被任意切开）、多行 data、注释行、CRLF/LF/CR 三种换行。
 */

export interface SSEMessage {
  event: string
  data: string
}

export class SSEParser {
  private buffer = ''
  private event = ''
  private dataLines: string[] = []
  private hasData = false

  /** 喂入一段文本，返回已经完整的事件。 */
  feed(chunk: string): SSEMessage[] {
    this.buffer += chunk
    const out: SSEMessage[] = []
    let start = 0
    for (;;) {
      let i = start
      while (i < this.buffer.length && this.buffer[i] !== '\n' && this.buffer[i] !== '\r') i++
      if (i >= this.buffer.length) break
      // 以 \r 结尾且后面还没有字符：无法判断是 \r 还是 \r\n，等下一块
      if (this.buffer[i] === '\r' && i === this.buffer.length - 1) break
      const line = this.buffer.slice(start, i)
      const skip = this.buffer[i] === '\r' && this.buffer[i + 1] === '\n' ? 2 : 1
      start = i + skip
      const msg = this.processLine(line)
      if (msg) out.push(msg)
    }
    this.buffer = this.buffer.slice(start)
    return out
  }

  /** 流结束时调用：派发残留的、未以空行结束的事件。 */
  flush(): SSEMessage[] {
    const out: SSEMessage[] = []
    if (this.buffer) {
      const msg = this.processLine(this.buffer.replace(/\r$/, ''))
      this.buffer = ''
      if (msg) out.push(msg)
    }
    const last = this.dispatch()
    if (last) out.push(last)
    return out
  }

  private processLine(line: string): SSEMessage | null {
    if (line === '') return this.dispatch()
    if (line.startsWith(':')) return null
    const idx = line.indexOf(':')
    const field = idx === -1 ? line : line.slice(0, idx)
    let value = idx === -1 ? '' : line.slice(idx + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') this.event = value
    else if (field === 'data') {
      this.dataLines.push(value)
      this.hasData = true
    }
    return null
  }

  private dispatch(): SSEMessage | null {
    if (!this.hasData && !this.event) return null
    const msg: SSEMessage = { event: this.event || 'message', data: this.dataLines.join('\n') }
    this.event = ''
    this.dataLines = []
    this.hasData = false
    return msg
  }
}

/** 逐块读取 Response.body 并回调每个完整事件。 */
export async function readSSE(
  body: ReadableStream<Uint8Array>,
  onMessage: (msg: SSEMessage) => void,
  signal?: AbortSignal
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  const parser = new SSEParser()
  const onAbort = (): void => {
    void reader.cancel().catch(() => undefined)
  }
  signal?.addEventListener('abort', onAbort)
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      for (const m of parser.feed(decoder.decode(value, { stream: true }))) onMessage(m)
    }
    for (const m of parser.feed(decoder.decode())) onMessage(m)
    for (const m of parser.flush()) onMessage(m)
  } finally {
    signal?.removeEventListener('abort', onAbort)
    try {
      reader.releaseLock()
    } catch {
      /* 读取器可能仍有挂起的读取，忽略 */
    }
  }
}
