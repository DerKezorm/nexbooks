/** Days as the person's own calendar has them (`YYYY-MM-DD`), for "Finished" and the reading stack. */

/** `YYYY-MM-DD` as a day in the language of the page ("12. Mai 2026"). The day is the person's own, no time zone is applied. */
export function dayText(day: string, language: string): string {
  const [year, month, date] = day.split('-').map(Number)
  if (!year || !month || !date) return day
  try {
    return new Date(year, month - 1, date).toLocaleDateString(language, { year: 'numeric', month: 'long', day: 'numeric' })
  } catch {
    return day
  }
}

export function localToday(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

