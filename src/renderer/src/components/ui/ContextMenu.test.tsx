// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useContextMenu } from './ContextMenu'

afterEach(cleanup)

function Host({ onPick, empty = false }: { onPick: () => void; empty?: boolean }) {
  const cm = useContextMenu()
  return (
    <div data-testid="host" onContextMenu={(e) => cm.open(e, empty ? [] : [{ key: 'a', label: '执行这个', onSelect: onPick }, { key: 's', separator: true }, { key: 'b', label: '不能用', disabled: true }])}>
      内容
      {cm.element}
    </div>
  )
}

describe('右键菜单', () => {
  it('右键弹出菜单，点选项执行并关闭；Esc 关闭；空菜单不拦截系统菜单', () => {
    const pick = vi.fn()
    render(<Host onPick={pick} />)
    const ev = fireEvent.contextMenu(screen.getByTestId('host'), { clientX: 30, clientY: 40 })
    expect(ev).toBe(false) // 已 preventDefault
    fireEvent.click(screen.getByRole('menuitem', { name: '执行这个' }))
    expect(pick).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('menu')).toBeNull()
    fireEvent.contextMenu(screen.getByTestId('host'))
    expect(screen.getByRole('menu')).toBeTruthy()
    expect((screen.getByRole('menuitem', { name: '不能用' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    cleanup()
    render(<Host onPick={pick} empty />)
    expect(fireEvent.contextMenu(screen.getByTestId('host'))).toBe(true) // 没有可用项：不拦截
    expect(screen.queryByRole('menu')).toBeNull()
  })
})
