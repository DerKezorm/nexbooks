import type { FileOut } from '../api/client'

/** Where Amazon takes a book from a person who sends it by hand. */
export const AMAZON_SEND = 'https://www.amazon.com/sendtokindle'

/** The EPUB a Kindle can take: there is one, and it is not copy-protected. */
export function sendableFile(files: FileOut[]): FileOut | undefined {
  return files.find((file) => file.format === 'epub' && !file.protected)
}
