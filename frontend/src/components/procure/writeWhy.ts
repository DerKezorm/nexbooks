/** Why nexbooks may not write into a library, in a few words: the reason beside a choice that is greyed out. */
export function shortWhy(problem: string, t: (key: string) => string): string {
  return t(`procure.formats.place.short.${['missing', 'read_only', 'no_permission'].includes(problem) ? problem : 'write_failed'}`)
}
