import { safeStorage } from 'electron'
import { existsSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { dirname } from 'node:path'

/**
 * 使用 Electron safeStorage（Windows 上为 DPAPI）加密保存敏感凭据。
 * 系统安全存储不可用时拒绝保存，绝不降级为明文落盘。
 */
export class SecretStore {
  constructor(private readonly file: string) {}

  private readAll(): Record<string, string> {
    if (!existsSync(this.file)) return {}
    try {
      if (!safeStorage.isEncryptionAvailable()) {
        console.error('系统安全存储暂不可用，读不到已保存的 API Key')
        return {}
      }
      const raw = readFileSync(this.file)
      const parsed = JSON.parse(safeStorage.decryptString(raw)) as unknown
      return parsed && typeof parsed === 'object' ? (parsed as Record<string, string>) : {}
    } catch (err) {
      // 读不出来时不要静默：界面只会显示“尚未保存 API Key”，没有任何线索（只记原因，不记内容）
      console.error('读取已保存的 API Key 失败：', err instanceof Error ? err.message : 'unknown error')
      return {}
    }
  }

  private writeAll(data: Record<string, string>): void {
    if (!safeStorage.isEncryptionAvailable()) {
      throw new Error('系统安全存储不可用，无法安全保存 API Key。')
    }
    mkdirSync(dirname(this.file), { recursive: true })
    if (Object.keys(data).length === 0) {
      rmSync(this.file, { force: true })
      return
    }
    writeFileSync(this.file, safeStorage.encryptString(JSON.stringify(data)))
  }

  get(name: string): string | null {
    return this.readAll()[name] ?? null
  }

  has(name: string): boolean {
    return this.get(name) !== null
  }

  set(name: string, value: string): void {
    const all = this.readAll()
    all[name] = value
    this.writeAll(all)
  }

  clear(name: string): void {
    const all = this.readAll()
    delete all[name]
    this.writeAll(all)
  }

  /**
   * 首次启动时把本机预置的默认 API Key 导入安全存储，然后删除预置文件，不留明文。
   * 预置文件 `.local/default-api.json`（{"api_key": "..."}）只在本机，不属于源码。
   * 已经有 Key 时什么也不做，不会覆盖用户之后自己改的 Key。
   */
  importSeed(seedFile: string, fields: Record<string, string>): boolean {
    if (!existsSync(seedFile)) return false
    try {
      const parsed = JSON.parse(readFileSync(seedFile, 'utf-8')) as Record<string, unknown>
      let imported = false
      for (const [field, name] of Object.entries(fields)) {
        const value = typeof parsed[field] === 'string' ? (parsed[field] as string).trim() : ''
        if (value && !this.has(name)) {
          this.set(name, value)
          imported = true
        }
      }
      rmSync(seedFile, { force: true })
      return imported
    } catch {
      return false
    }
  }
}
