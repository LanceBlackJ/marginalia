import { contextBridge, ipcRenderer } from 'electron'
import { IPC, type BackendStatus, type PaperDeskBridge } from '../shared/bridge'

const bridge: PaperDeskBridge = {
  getBackendInfo: () => ipcRenderer.invoke(IPC.backendInfo),
  getBackendStatus: () => ipcRenderer.invoke(IPC.backendStatus),
  onBackendStatus(cb) {
    const handler = (_e: unknown, status: BackendStatus, detail?: string): void => cb(status, detail)
    ipcRenderer.on(IPC.backendStatusEvent, handler)
    return () => {
      ipcRenderer.removeListener(IPC.backendStatusEvent, handler)
    }
  },
  retryBackend: () => ipcRenderer.invoke(IPC.backendRetry),
  pickPdfFiles: () => ipcRenderer.invoke(IPC.pickPdfFiles),
  pickSupplementFiles: () => ipcRenderer.invoke(IPC.pickSupplementFiles),
  pickFolder: () => ipcRenderer.invoke(IPC.pickFolder),
  setApiKey: (key) => ipcRenderer.invoke(IPC.setApiKey, key),
  clearApiKey: () => ipcRenderer.invoke(IPC.clearApiKey),
  hasApiKey: () => ipcRenderer.invoke(IPC.hasApiKey),
  setS2Key: (key) => ipcRenderer.invoke(IPC.setS2Key, key),
  clearS2Key: () => ipcRenderer.invoke(IPC.clearS2Key),
  hasS2Key: () => ipcRenderer.invoke(IPC.hasS2Key),
  openExternal: (url) => ipcRenderer.invoke(IPC.openExternal, url),
  getAppInfo: () => ipcRenderer.invoke(IPC.appInfo)
}

contextBridge.exposeInMainWorld('paperdesk', bridge)
