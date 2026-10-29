/**
 * Why this exists: the people reading this app may be older themselves
 * (her daughter is sixty), often on a small phone, often worried. So:
 * large type by default, high contrast on a warm, quiet background, and
 * status shown with a word *and* a mark, never by colour alone.
 *
 * Every text colour here is at least 4.5:1 against the surface it sits
 * on (WCAG AA). Change one, re-check it.
 */
export const color = {
  bg: '#F6F1E7',
  surface: '#FFFFFF',
  text: '#1F2328',
  muted: '#545961',
  /** Lighter than `muted` so an example never reads as typed text; still 4.5:1. */
  placeholder: '#74777C',
  border: '#D6CEBD',
  primary: '#2F5D50',
  onPrimary: '#FFFFFF',
  danger: '#9B2423',
  taken: '#1D6A39',
  takenBg: '#E3F1E7',
  missed: '#9B2423',
  missedBg: '#F8E3E1',
  pending: '#6A5300',
  pendingBg: '#F5EDCF',
  focus: '#1B4FA0',
} as const;

export const font = {
  title: 28,
  heading: 23,
  body: 20,
  small: 17,
} as const;

export const space = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 } as const;

/** Minimum height of anything tappable (Apple says 44, we give more). */
export const touch = 56;

export const radius = 14;
