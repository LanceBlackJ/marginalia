import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui'

interface Props {
  children: ReactNode
  /** 点“重试”：清掉出错状态，让页面重新渲染 */
  onReset: () => void
  onHome: () => void
  /** 换到别的页面时（这个值变了）自动清掉出错状态 */
  resetKey: string
}

interface State {
  error: Error | null
}

class Boundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 留在控制台里，方便排查；界面上只给一句人话
    console.error('页面出错', error, info.componentStack)
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children
    return (
      <div className="pd-page">
        <div style={{ maxWidth: 560, margin: '80px auto 0' }}>
          <h1 className="pd-page-title">这个页面出错了</h1>
          <p className="pd-muted" style={{ margin: '8px 0 16px' }}>
            你的论文和笔记都在，没有受影响。可以重试一次，或者回到文献库。
          </p>
          <p className="pd-aux pd-muted" style={{ marginBottom: 20, wordBreak: 'break-word' }}>
            {this.state.error.message}
          </p>
          <div className="pd-row">
            <Button
              variant="primary"
              onClick={() => {
                this.setState({ error: null })
                this.props.onReset()
              }}
            >
              重试
            </Button>
            <Button
              onClick={() => {
                this.setState({ error: null })
                this.props.onHome()
              }}
            >
              回到文献库
            </Button>
          </div>
        </div>
      </div>
    )
  }
}

/** 页面级错误边界：某个页面渲染出错时只显示这个提示，不让整个应用白屏；换到别的页面会自动恢复。 */
export function PageBoundary({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()
  return (
    <Boundary resetKey={location.pathname} onReset={() => undefined} onHome={() => navigate('/library')}>
      {children}
    </Boundary>
  )
}
