/**
 * Why this exists: the seam. Screens import from here and nowhere else in
 * Firebase — so the UI can be read, reviewed and changed without knowing
 * the SDK, and the SDK (or the backend) can be swapped behind these
 * names without touching a screen. Lost: calling the SDK from components
 * with hooks like react-firebase-hooks (fast to write, but every screen
 * then encodes collection paths and Timestamp handling, and none of it
 * runs in a plain Node test).
 */
export * from './auth';
export * from './devices';
export * from './google';
export * from './messages';
export * from './push';
export * from './reminders';
export * from './time';
export * from './today';
export * from './turns';
export * from './types';
export * from './users';
