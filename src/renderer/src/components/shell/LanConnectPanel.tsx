import { FormEvent, useState } from 'react'
import { Button, Input } from '@/components/ui'

export function LanConnectPanel({ onConnect, detail }: { onConnect: (url: string) => void; detail?: string }) {
  const [address, setAddress] = useState('')
  const submit = (event: FormEvent): void => {
    event.preventDefault()
    if (address.trim()) onConnect(address)
  }
  return (
    <div className="pd-lan-connect">
      <div className="pd-lan-connect-mark">Marginalia</div>
      <h1>连接电脑端</h1>
      <p>在电脑端 Marginalia 的设置里查看局域网地址，输入电脑 IP 和端口即可。</p>
      <form onSubmit={submit} className="pd-lan-connect-form">
        <Input value={address} onChange={setAddress} placeholder="例如 192.168.1.20:8765" aria-label="电脑端地址" autoFocus />
        <Button type="submit" variant="primary" disabled={!address.trim()}>连接</Button>
      </form>
      {detail && <p className="pd-lan-connect-error">{detail}</p>}
      <p className="pd-lan-connect-hint">手机和电脑需要连接同一个 Wi‑Fi。连接后，手机会直接读取电脑上的文献库。</p>
    </div>
  )
}
