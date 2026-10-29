/**
 * Why this exists: the data layer is the seam, and a seam that needs
 * React Native to run isn't one. So its tests run in plain Node against
 * the Firebase emulators (firebase.test.json). This config deliberately
 * has no jest-expo preset and no RN mocks: a src/data import that pulls
 * React Native in would break the run. Lost: jest-expo, which is right
 * for component tests, but would hide exactly that mistake.
 * ts-jest only transpiles here; `npx tsc --noEmit` is the type check.
 */
/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/tests'],
  setupFiles: ['<rootDir>/tests/env.ts'],
  transform: {
    '^.+\\.tsx?$': [
      'ts-jest',
      {
        tsconfig: {
          module: 'commonjs',
          moduleResolution: 'node10',
          target: 'es2022',
          esModuleInterop: true,
          isolatedModules: true,
          strict: true,
        },
        diagnostics: false,
      },
    ],
  },
  testTimeout: 30000,
};
