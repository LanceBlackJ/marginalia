import { api, apiRequest } from './api'
import { hasBridge } from './backend'
import { usePathPrompt } from '@/stores/pathPromptStore'

/** 原生能力封装：Electron 下走 IPC；Web 调试模式下做最小降级。 */

export async function pickPdfFiles(): Promise<string[]> {
  if (hasBridge()) return window.paperdesk!.pickPdfFiles()
  const text = await usePathPrompt.getState().ask({
    title: '输入 PDF 路径',
    label: 'Web 调试模式没有原生文件对话框。每行输入一个 PDF 的绝对路径：',
    placeholder: 'C:\\papers\\example.pdf',
    multiline: true
  })
  return (text ?? '')
    .split(/\r?\n/)
    .map((s) => s.trim().replace(/^"|"$/g, ''))
    .filter(Boolean)
}

export async function pickSupplementFiles(): Promise<string[]> {
  if (hasBridge()) return window.paperdesk!.pickSupplementFiles()
  const text = await usePathPrompt.getState().ask({
    title: '输入补充材料路径',
    label: 'Web 调试模式没有原生文件对话框。每行输入一个文件的绝对路径，支持 PDF、表格、文档、压缩包等：',
    placeholder: 'C:\\papers\\supplement.pdf',
    multiline: true
  })
  return (text ?? '')
    .split(/\r?\n/)
    .map((s) => s.trim().replace(/^"|"$/g, ''))
    .filter(Boolean)
}

export async function pickFolder(): Promise<string | null> {
  if (hasBridge()) return window.paperdesk!.pickFolder()
  const text = await usePathPrompt.getState().ask({
    title: '输入文件夹路径',
    label: 'Web 调试模式没有原生文件夹对话框。请输入文件夹的绝对路径：',
    placeholder: 'C:\\papers'
  })
  return text?.trim().replace(/^"|"$/g, '') || null
}

export async function setApiKey(key: string): Promise<void> {
  if (hasBridge()) return window.paperdesk!.setApiKey(key)
  // 仅 Web 调试：直接推给后端内存（不落盘）
  await api.settings.putSecret(key)
}

export async function clearApiKey(): Promise<void> {
  if (hasBridge()) return window.paperdesk!.clearApiKey()
  await api.settings.deleteSecret()
}

/** 可选的 Semantic Scholar API Key：Electron 下经主进程加密保存；Web 调试直接推给后端内存。 */
export async function setS2Key(key: string): Promise<void> {
  if (hasBridge()) return window.paperdesk!.setS2Key(key)
  await apiRequest('PUT', '/api/settings/s2-key', { body: { api_key: key } })
}

export async function clearS2Key(): Promise<void> {
  if (hasBridge()) return window.paperdesk!.clearS2Key()
  await apiRequest('DELETE', '/api/settings/s2-key')
}

export async function hasS2Key(): Promise<boolean> {
  if (hasBridge()) return window.paperdesk!.hasS2Key()
  return (await apiRequest<{ has_key: boolean }>('GET', '/api/settings/s2-key')).has_key
}

export async function openExternal(url: string): Promise<void> {
  if (hasBridge()) return window.paperdesk!.openExternal(url)
  if (/^https?:\/\//i.test(url)) window.open(url, '_blank', 'noopener,noreferrer')
  else if (/^mailto:/i.test(url)) window.location.href = url
}

/** 打开内置的论文下载浏览器：可以在里面登录，点到的 PDF 会自动导入。Web 调试模式没有这个窗口，退回系统浏览器。 */
export async function openPaperBrowser(url: string): Promise<void> {
  if (hasBridge()) return window.paperdesk!.openPaperBrowser(url)
  return openExternal(url)
}

export async function getDataDirHint(): Promise<string | null> {
  if (hasBridge()) return (await window.paperdesk!.getAppInfo()).dataDir
  return null
}
