import { app } from 'electron'
import { join } from 'node:path'

/** 应用根目录（开发期为仓库根；打包后为 app.asar 所在位置）。 */
export function appRoot(): string {
  return app.getAppPath()
}

/** 数据目录：环境变量 > 开发期 <repo>/.devdata > 打包后 userData/data。 */
export function resolveDataDir(): string {
  const fromEnv = process.env.PAPERDESK_DATA_DIR
  if (fromEnv && fromEnv.trim()) return fromEnv.trim()
  return app.isPackaged ? join(app.getPath('userData'), 'data') : join(appRoot(), '.devdata')
}
