/// <reference types="vite/client" />
import type { PaperDeskBridge } from '@shared/bridge'

declare global {
  interface Window {
    paperdesk?: PaperDeskBridge
  }
}

export {}
