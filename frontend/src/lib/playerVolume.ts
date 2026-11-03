/**
 * The volume of the player: this device's own (`localStorage`, if the browser lets it), not the account's, because a phone,
 * a laptop and a box at the stereo each want their own.
 *
 * - The level is from 0 to 1; mute is apart from it, so that the sound comes back at the level it had.
 * - Safari on iPhone and iPad lets no page set the volume (only the buttons of the device do): there the player shows no
 *   control (`volumeSettable`).
 */

const STORAGE_KEY = 'nexbooks.player.volume'

export type Volume = { level: number; muted: boolean }

export const VOLUME_STEP = 0.05

export function clampVolume(level: number): number {
  if (!Number.isFinite(level)) return 1
  return Math.round(Math.min(1, Math.max(0, level)) * 100) / 100
}

export function loadVolume(): Volume {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as Partial<Volume> | null
    if (stored && typeof stored.level === 'number') return { level: clampVolume(stored.level), muted: stored.muted === true }
  } catch {
    // No storage, or something else under the key: full volume.
  }
  return { level: 1, muted: false }
}

export function saveVolume(volume: Volume): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(volume))
  } catch {
    // No storage: the volume holds for this page.
  }
}

/** Whether a page may set the volume here: an element that keeps 1 after it was set to a half belongs to the device. */
export function volumeSettable(): boolean {
  try {
    const probe = document.createElement('audio')
    probe.volume = 0.5
    return Math.abs(probe.volume - 0.5) < 0.01
  } catch {
    return false
  }
}
