// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ThinkingBlock } from './ThinkingBlock'

afterEach(cleanup)

describe('ThinkingBlock', () => {
  it('生成中、回答还没出来：自动展开，标题是“思考中…”', () => {
    render(<ThinkingBlock text="先看第三节" live />)
    expect(screen.getByText('先看第三节')).toBeTruthy()
    expect(screen.getByText(/思考中/)).toBeTruthy()
  })

  it('回答开始后收起，点开能再看到', () => {
    render(<ThinkingBlock text="先看第三节" live answering />)
    expect(screen.queryByText('先看第三节')).toBeNull()
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText('先看第三节')).toBeTruthy()
  })

  it('保存好的卡片里默认折叠，并说明不是论文原文', () => {
    render(<ThinkingBlock text="x" />)
    expect(screen.queryByText('x')).toBeNull()
    expect(screen.getByText(/不是论文原文/)).toBeTruthy()
  })
})
