import * as pdfjs from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import 'pdfjs-dist/web/pdf_viewer.css'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

export type { PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist'
export { pdfjs }

/** pdf.js 的资源文件随应用打包（public/pdfjs），需要用相对文档的绝对 URL，worker 才能正确解析。 */
function assetUrl(path: string): string {
  return new URL(`${import.meta.env.BASE_URL}pdfjs/${path}`, document.baseURI).href
}

export async function loadPdfDocument(data: ArrayBuffer): Promise<pdfjs.PDFDocumentProxy> {
  const task = pdfjs.getDocument({
    data: new Uint8Array(data),
    cMapUrl: assetUrl('cmaps/'),
    cMapPacked: true,
    standardFontDataUrl: assetUrl('standard_fonts/'),
    wasmUrl: assetUrl('wasm/'),
    iccUrl: assetUrl('iccs/')
  })
  return task.promise
}
