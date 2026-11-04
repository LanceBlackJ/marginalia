// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useBack } from './useBack'

afterEach(cleanup)

function Where() {
  const loc = useLocation()
  return <div data-testid="where">{loc.pathname}</div>
}

function Paper() {
  const back = useBack('/library')
  const navigate = useNavigate()
  return (
    <>
      <Where />
      <button onClick={back}>返回</button>
      <button onClick={() => navigate('/reader/p1')}>去阅读</button>
    </>
  )
}

function Reader() {
  const back = useBack('/paper/p1')
  return (
    <>
      <Where />
      <button onClick={back}>返回</button>
    </>
  )
}

const app = (initial: string) => (
  <MemoryRouter initialEntries={[initial]}>
    <Routes>
      <Route path="/library" element={<Where />} />
      <Route path="/paper/:id" element={<Paper />} />
      <Route path="/reader/:id" element={<Reader />} />
    </Routes>
  </MemoryRouter>
)

describe('统一的返回', () => {
  it('直接打开、没有上一页时，回到这一页的上级页面', () => {
    render(app('/paper/p1'))
    fireEvent.click(screen.getByText('返回'))
    expect(screen.getByTestId('where').textContent).toBe('/library')
  })

  it('从别的页面进来的，返回就回到那一页（而不是写死的上级页）', () => {
    render(app('/paper/p1'))
    fireEvent.click(screen.getByText('去阅读'))
    expect(screen.getByTestId('where').textContent).toBe('/reader/p1')
    fireEvent.click(screen.getByText('返回'))
    expect(screen.getByTestId('where').textContent).toBe('/paper/p1')
  })

  it('阅读页是直接打开的，返回就去这篇论文的详情页', () => {
    render(app('/reader/p1'))
    fireEvent.click(screen.getByText('返回'))
    expect(screen.getByTestId('where').textContent).toBe('/paper/p1')
  })
})
