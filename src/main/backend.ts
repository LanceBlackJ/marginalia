import { spawn, execFile, type ChildProcess } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { createServer } from 'node:net'
import { randomBytes } from 'node:crypto'
import { createWriteStream, existsSync, mkdirSync, type WriteStream } from 'node:fs'
import { join } from 'node:path'
import type { BackendInfo, BackendStatus } from '../shared/bridge'

interface BackendOptions {
  appRoot: string
  dataDir: string
  isPackaged: boolean
  resourcesPath: string
}

const READY_TIMEOUT_MS = 60_000
const POLL_INTERVAL_MS = 300

function getFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address()
      if (addr && typeof addr === 'object') {
        const port = addr.port
        srv.close(() => resolve(port))
      } else {
        srv.close(() => reject(new Error('无法获取空闲端口')))
      }
    })
  })
}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

/** 管理本地 Python 后端进程：拉起、就绪探测、退出清理、状态上报。 */
export class BackendManager extends EventEmitter {
  status: BackendStatus = 'starting'
  detail: string | undefined
  info: BackendInfo | null = null

  private proc: ChildProcess | null = null
  private log: WriteStream | null = null
  private tail: string[] = []
  private token = ''
  private stopping = false
  private generation = 0

  constructor(private readonly opts: BackendOptions) {
    super()
  }

  private setStatus(status: BackendStatus, detail?: string): void {
    this.status = status
    this.detail = detail
    this.emit('status', status, detail)
  }

  /** 打包后的后端：PyInstaller 产物（resources/backend/paperdesk-backend/paperdesk-backend.exe）。 */
  private packagedExe(): string {
    return join(this.opts.resourcesPath, 'backend', 'paperdesk-backend', 'paperdesk-backend.exe')
  }

  private resolvePython(): string {
    const fromEnv = process.env.PAPERDESK_PYTHON
    if (fromEnv && existsSync(fromEnv)) return fromEnv
    const candidates = [
      join(this.opts.appRoot, 'backend', '.venv', 'Scripts', 'python.exe'),
      join(this.opts.resourcesPath, 'backend', 'python', 'python.exe')
    ]
    for (const c of candidates) if (existsSync(c)) return c
    return 'python'
  }

  private backendDir(): string {
    const dev = join(this.opts.appRoot, 'backend')
    if (existsSync(join(dev, 'paperdesk', '__main__.py'))) return dev
    return join(this.opts.resourcesPath, 'backend')
  }

  /** 决定怎么启动后端：打包版直接运行 exe；开发期用 venv 的 python -m paperdesk。 */
  private resolveLaunch(port: number): { cmd: string; args: string[]; cwd: string } | null {
    const common = ['--host', '127.0.0.1', '--port', String(port), '--data-dir', this.opts.dataDir]
    if (this.opts.isPackaged) {
      const exe = this.packagedExe()
      if (!existsSync(exe)) return null
      return { cmd: exe, args: common, cwd: join(this.opts.resourcesPath, 'backend', 'paperdesk-backend') }
    }
    const cwd = this.backendDir()
    if (!existsSync(join(cwd, 'paperdesk', '__main__.py'))) return null
    return { cmd: this.resolvePython(), args: ['-X', 'utf8', '-m', 'paperdesk', ...common], cwd }
  }

  private writeLog(line: string): void {
    const redacted = this.token ? line.split(this.token).join('***') : line
    this.tail.push(redacted)
    if (this.tail.length > 40) this.tail.shift()
    this.log?.write(`${new Date().toISOString()} ${redacted}\n`)
  }

  async start(): Promise<void> {
    const gen = ++this.generation
    this.stopping = false
    this.tail = []
    this.setStatus('starting')

    try {
      const logsDir = join(this.opts.dataDir, 'logs')
      mkdirSync(logsDir, { recursive: true })
      this.log?.end()
      this.log = createWriteStream(join(logsDir, 'backend.log'), { flags: 'a' })

      const port = await getFreePort()
      this.token = randomBytes(24).toString('hex')
      const launch = this.resolveLaunch(port)
      if (!launch) {
        this.setStatus(
          'failed',
          this.opts.isPackaged
            ? `安装目录里缺少后端程序（${this.packagedExe()}）。请重新安装 Marginalia。`
            : '未找到后端程序。请先运行 npm run backend:setup。'
        )
        return
      }
      this.writeLog(`启动后端：${launch.cmd} --port ${port}`)

      const proc = spawn(launch.cmd, launch.args, {
        cwd: launch.cwd,
        windowsHide: true,
        env: {
          ...process.env,
          PAPERDESK_TOKEN: this.token,
          PYTHONUNBUFFERED: '1',
          PYTHONIOENCODING: 'utf-8'
        }
      })
      this.proc = proc

      const onData = (buf: Buffer): void => {
        for (const line of buf.toString('utf8').split(/\r?\n/)) if (line.trim()) this.writeLog(line)
      }
      proc.stdout?.on('data', onData)
      proc.stderr?.on('data', onData)

      let exited = false
      proc.on('error', (err) => {
        exited = true
        this.writeLog(`进程启动失败：${err.message}`)
        if (gen === this.generation && !this.stopping) {
          this.setStatus('failed', `无法启动后端进程：${err.message}`)
        }
      })
      proc.on('exit', (code, signal) => {
        exited = true
        this.writeLog(`后端进程退出 code=${code} signal=${signal}`)
        if (gen !== this.generation || this.stopping) return
        const hint = this.tail.slice(-6).join('\n')
        this.setStatus('failed', `后端进程意外退出（code=${code ?? signal}）。\n${hint}`)
      })

      const baseUrl = `http://127.0.0.1:${port}`
      const deadline = Date.now() + READY_TIMEOUT_MS
      while (Date.now() < deadline) {
        if (gen !== this.generation) return
        if (exited) return
        try {
          const res = await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(2000) })
          if (res.ok) {
            this.info = { baseUrl, token: this.token }
            this.setStatus('ready')
            return
          }
        } catch {
          /* 尚未就绪，继续轮询 */
        }
        await sleep(POLL_INTERVAL_MS)
      }
      if (gen === this.generation) {
        this.setStatus('failed', `后端在 ${READY_TIMEOUT_MS / 1000} 秒内未就绪。\n${this.tail.slice(-6).join('\n')}`)
        await this.killProcess()
      }
    } catch (err) {
      this.setStatus('failed', `后端启动异常：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  async restart(): Promise<void> {
    await this.stop()
    this.info = null
    await this.start()
  }

  async stop(): Promise<void> {
    this.stopping = true
    this.generation++
    await this.killProcess()
    this.log?.end()
    this.log = null
  }

  private killProcess(): Promise<void> {
    const proc = this.proc
    this.proc = null
    if (!proc || proc.exitCode !== null || !proc.pid) return Promise.resolve()
    return new Promise((resolve) => {
      const done = (): void => resolve()
      if (process.platform === 'win32') {
        // 结束整个进程树（uvicorn 可能派生子进程）
        execFile('taskkill', ['/pid', String(proc.pid), '/T', '/F'], { windowsHide: true }, done)
      } else {
        proc.once('exit', done)
        proc.kill('SIGTERM')
        setTimeout(() => {
          proc.kill('SIGKILL')
          done()
        }, 3000).unref()
      }
    })
  }

  /** 同步地尽力结束进程（用于 will-quit 等不能等待的场景）。 */
  killNow(): void {
    this.stopping = true
    const pid = this.proc?.pid
    if (!pid) return
    if (process.platform === 'win32') {
      execFile('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true }, () => undefined)
    } else {
      this.proc?.kill('SIGKILL')
    }
  }
}
