// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { PageBoundary } from './PageBoundary'

afterEach(cleanup)

function Boom(): never {
  throw new Error('渲染时出错了')
}

function Home() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/bad')}>去出错的页面</button>
}

const app = (
  <MemoryRouter initialEntries={['/']}>
    <PageBoundary>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/bad" element={<Boom />} />
        <Route path="/library" element={<div>文献库</div>} />
      </Routes>
    </PageBoundary>
  </MemoryRouter>
)

describe('页面错误边界', () => {
  it('页面渲染出错时显示提示和出错原因，而不是整页白屏；点“回到文献库”可以恢复', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    // React 会把“已被边界接住的渲染错误”再向 window 报告一次，测试里拦掉，免得被当成未处理的错误
    const swallow = (e: Event): void => e.preventDefault()
    window.addEventListener('error', swallow)
    render(app)
    fireEvent.click(screen.getByText('去出错的页面'))
    expect(screen.getByText('这个页面出错了')).toBeTruthy()
    expect(screen.getByText('渲染时出错了')).toBeTruthy()
    fireEvent.click(screen.getByText('回到文献库'))
    expect(screen.getByText('文献库')).toBeTruthy()
    window.removeEventListener('error', swallow)
  })
})
