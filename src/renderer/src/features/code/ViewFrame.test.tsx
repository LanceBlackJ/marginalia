// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { Disclosure, StepSection, ViewFrame, ViewHead, ViewSection } from './ViewFrame'

afterEach(cleanup)

describe('ViewFrame', () => {
  it('页面头：标题、一行说明和右侧操作都渲染，narrow 宽度写在 data 属性里', () => {
    const { container } = render(
      <ViewFrame width="narrow">
        <ViewHead title="项目概览" desc="只来自静态分析" actions={<button type="button">重新检查</button>} />
        <ViewSection title="语言" hint="按代码行数" strong>
          <p>cython</p>
        </ViewSection>
      </ViewFrame>
    )
    expect(screen.getByRole('heading', { level: 2, name: '项目概览' })).toBeTruthy()
    expect(screen.getByText('只来自静态分析')).toBeTruthy()
    expect(screen.getByRole('button', { name: '重新检查' })).toBeTruthy()
    expect(container.querySelector('.pd-vf')?.getAttribute('data-width')).toBe('narrow')
    expect(container.querySelector('.pd-vf-section')?.getAttribute('data-strong')).toBe('true')
    expect(screen.getByRole('heading', { level: 3, name: '语言' })).toBeTruthy()
  })

  it('步骤分区：只有当前步骤展开，点击其它步骤标题切换', () => {
    function Steps() {
      const [open, setOpen] = useState<'a' | 'b' | 'c'>('a')
      return (
        <div>
          {(['a', 'b', 'c'] as const).map((k, i) => (
            <StepSection key={k} index={i + 1} title={`步骤 ${k}`} summary={`摘要 ${k}`} open={open === k} onToggle={() => setOpen(k)}>
              <p>内容 {k}</p>
            </StepSection>
          ))}
        </div>
      )
    }
    render(<Steps />)
    expect(screen.getByText('内容 a')).toBeTruthy()
    expect(screen.queryByText('内容 b')).toBeNull()
    expect(screen.getByRole('button', { name: /步骤 a/ }).getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('button', { name: /步骤 b/ }).getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(screen.getByRole('button', { name: /步骤 b/ }))
    expect(screen.queryByText('内容 a')).toBeNull()
    expect(screen.getByText('内容 b')).toBeTruthy()
  })

  it('折叠项：默认收起，点击后展开并可再次收起', () => {
    render(
      <Disclosure label="为什么不能在这里运行">
        <p>没有实现执行器</p>
      </Disclosure>
    )
    const btn = screen.getByRole('button', { name: /为什么不能在这里运行/ })
    expect(screen.queryByText('没有实现执行器')).toBeNull()
    fireEvent.click(btn)
    expect(screen.getByText('没有实现执行器')).toBeTruthy()
    fireEvent.click(btn)
    expect(screen.queryByText('没有实现执行器')).toBeNull()
  })

  it('步骤切换回调只在点击时触发', () => {
    const onToggle = vi.fn()
    render(
      <StepSection index={2} title="复现计划" open={false} onToggle={onToggle}>
        <p>x</p>
      </StepSection>
    )
    expect(onToggle).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /复现计划/ }))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })
})
