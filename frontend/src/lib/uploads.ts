/** What the upload of a book takes: the formats and the limits the server keeps
 * (``backend/app/services/procure/uploads.py``), known before an upload begins. */

const EBOOK = ['epub', 'pdf', 'azw3', 'azw', 'mobi']
const AUDIO = ['m4b', 'mp3', 'm4a', 'flac', 'ogg', 'oga', 'opus']
const BESIDE = ['cover.jpg', 'cover.jpeg', 'cover.png', 'folder.jpg', 'metadata.json', 'metadata.opf']
export const ACCEPT = [...EBOOK, ...AUDIO, 'zip'].map((ending) => `.${ending}`).join(',')
export const EBOOK_BYTES = 500 * 1024 ** 2
export const FILE_BYTES = 4 * 1024 ** 3

/** What a file is, by its name: an e-book, a recording, a file beside a book, a ZIP; empty for anything else. */
export function kindOfName(name: string): 'ebook' | 'audio' | 'beside' | 'zip' | '' {
  const lowered = name.split('/').pop()?.toLowerCase() ?? ''
  if (BESIDE.includes(lowered)) return 'beside'
  const ending = lowered.includes('.') ? (lowered.split('.').pop() ?? '') : ''
  if (EBOOK.includes(ending)) return 'ebook'
  if (AUDIO.includes(ending)) return 'audio'
  if (ending === 'zip') return 'zip'
  return ''
}
