// The parts of foliate-js that nexbooks uses, as far as nexbooks types them. The library itself is plain JavaScript.

declare module '*/foliate-js/view.js' {
  export type TocNode = { label?: string; href?: string; id?: number; subitems?: TocNode[] }

  export type FoliateBook = {
    sections: { id?: string | number; linear?: string; size?: number; load: () => Promise<string | null>; unload?: () => void; createDocument?: () => Promise<Document> }[]
    toc?: TocNode[]
    metadata?: { title?: string | Record<string, string>; language?: string | string[] }
    dir?: string
    rendition?: { layout?: string }
    transformTarget?: EventTarget
    isExternal?: (uri: string) => boolean
    clean?: (text: string, type: string) => Promise<{ text: string; type: string }>
    guard?: (bytes: Uint8Array) => Uint8Array
    destroy?: () => void
  }

  export type Relocation = {
    reason?: string
    fraction: number
    section: { current: number; total: number }
    location: { current: number; next: number; total: number }
    tocItem?: TocNode | null
    pageItem?: TocNode | null
    cfi: string
    range?: Range
  }

  export type SearchResult =
    | 'done'
    | { progress: number }
    | { label: string; subitems: { cfi: string; excerpt: { pre: string; match: string; post: string } }[] }

  export interface Renderer extends HTMLElement {
    setStyles?: (css: string | [string, string]) => void
    next: (distance?: number) => Promise<void>
    prev: (distance?: number) => Promise<void>
    getContents: () => { index: number; doc: Document; overlayer?: unknown }[]
    scrolled?: boolean
    heads?: HTMLElement[]
    feet?: HTMLElement[]
  }

  export class View extends HTMLElement {
    book: FoliateBook
    renderer: Renderer
    isFixedLayout: boolean
    lastLocation?: Relocation
    open(book: FoliateBook): Promise<void>
    close(): void
    init(options: { lastLocation?: string | null; showTextStart?: boolean }): Promise<void>
    goTo(target: string | number): Promise<unknown>
    goToFraction(fraction: number): Promise<void>
    goLeft(): Promise<void>
    goRight(): Promise<void>
    next(): Promise<void>
    prev(): Promise<void>
    getSectionFractions(): number[]
    resolveNavigation(target: unknown): { index: number } | undefined
    search(options: { query: string; index?: number; matchCase?: boolean }): AsyncGenerator<SearchResult>
    clearSearch(): void
    getCFI(index: number, range?: Range): string
  }
}

declare module '*/foliate-js/epub.js' {
  import type { FoliateBook } from '*/foliate-js/view.js'
  export class EPUB {
    constructor(loader: {
      loadText: (name: string) => Promise<string | null>
      loadBlob: (name: string, type?: string) => Promise<Blob | null>
      getSize: (name: string) => number
      sha1?: (text: string) => Promise<Uint8Array>
    })
    init(): Promise<FoliateBook & EPUB>
  }
}

declare module '*/foliate-js/mobi.js' {
  import type { FoliateBook } from '*/foliate-js/view.js'
  export const isMOBI: (file: Blob) => Promise<boolean>
  export class MOBI {
    constructor(options: { unzlib: (data: Uint8Array) => Uint8Array })
    open(file: Blob): Promise<FoliateBook>
  }
}

declare module '*/foliate-js/vendor/fflate.js' {
  export const unzlibSync: (data: Uint8Array) => Uint8Array
}

declare module '*/foliate-js/vendor/zip.js' {
  export function configure(options: Record<string, unknown>): void
  export class BlobReader {
    constructor(blob: Blob)
  }
  export class ZipReader {
    constructor(reader: BlobReader)
    getEntries(): Promise<
      {
        filename: string
        directory: boolean
        uncompressedSize: number
        compressedSize: number
        getData: (writer: WritableStream<Uint8Array>, options?: { signal?: AbortSignal }) => Promise<unknown>
      }[]
    >
    close(): Promise<void>
  }
}

declare module '*/foliate-js/overlayer.js' {
  export class Overlayer {
    static outline: (rects: DOMRectList, options?: Record<string, unknown>) => SVGElement
    static highlight: (rects: DOMRectList, options?: Record<string, unknown>) => SVGElement
  }
}
