import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-500.css'
import '@fontsource/inter/latin-600.css'
import '@/styles/tokens.css'
import '@/styles/base.css'
import '@/styles/ui.css'
import { App } from './App'
import { installLatexCopy } from '@/lib/copyLatex'

installLatexCopy() // 复制含公式的文字时得到 LaTeX 源码

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
)
