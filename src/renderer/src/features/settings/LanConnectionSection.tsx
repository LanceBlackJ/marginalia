import { useState } from 'react'
import { Button, Select } from '@/components/ui'
import { useBackendStore } from '@/stores/backendStore'
import { QRCodeSVG } from 'qrcode.react'

export function LanConnectionSection() {
  const lanUrl = useBackendStore((s) => s.lanUrl)
  const lanUrls = useBackendStore((s) => s.lanUrls)
  const selectLanUrl = useBackendStore((s) => s.selectLanUrl)
  const [copied, setCopied] = useState(false)
  if (!lanUrl) return null
  const copy = async (): Promise<void> => {
    await navigator.clipboard?.writeText(lanUrl)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }
  return (
    <section className="pd-panel" id="lan-connection">
      <div className="pd-panel-title">手机连接</div>
      <div className="pd-panel-body">
        <p>手机和电脑连接同一个 Wi‑Fi 后，在手机端输入下面的地址：</p>
        {lanUrls.length > 1 && (
          <Select
            aria-label="选择手机连接网卡地址"
            value={lanUrl}
            onChange={selectLanUrl}
            options={lanUrls.map((url) => ({ value: url, label: url }))}
          />
        )}
        <code>{lanUrl}</code>
        <div className="pd-lan-qr" aria-label="手机扫描连接二维码">
          <QRCodeSVG value={lanUrl} size={180} bgColor="#ffffff" fgColor="#111111" includeMargin />
        </div>
        <p className="pd-muted">手机端点“扫描电脑二维码”，对准这张二维码即可自动填入地址。</p>
        <div style={{ marginTop: 12 }}><Button size="sm" onClick={() => void copy()}>{copied ? '已复制' : '复制地址'}</Button></div>
        <p className="pd-muted" style={{ marginTop: 12 }}>手机端读取的是本机文献库。电脑端重新构建并重启后，手机刷新页面即可使用新版本。</p>
      </div>
    </section>
  )
}
