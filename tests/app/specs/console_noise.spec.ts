import { test, expect } from '../fixtures';
import { isNoise } from '../helpers/console_noise';

// F8: the smoke test must not call the machine's own faults an app error, and must keep calling real ones.
// A Windows runner that runs out of socket buffers makes the browser log net::ERR_NO_BUFFER_SPACE.

test.describe('console noise the smoke test ignores', () => {
  test('the runner running out of socket buffers is not an app error', () => {
    expect(isNoise('Failed to load resource: net::ERR_NO_BUFFER_SPACE')).toBe(true);
  });

  test('the old noise is still ignored', () => {
    for (const m of ['WebGL: INVALID_OPERATION', 'GL_INVALID_ENUM', 'GET /favicon.ico 404']) expect(isNoise(m), m).toBe(true);
  });

  test('a real failed fetch or script error is still an app error', () => {
    for (const m of ['Failed to load resource: net::ERR_CONNECTION_REFUSED', 'Failed to load resource: the server responded with a status of 500 (INTERNAL SERVER ERROR)',
      'Failed to load resource: the server responded with a status of 404 (NOT FOUND)', 'TypeError: x is not a function', 'Uncaught ReferenceError: y is not defined']) {
      expect(isNoise(m), m).toBe(false);
    }
  });
});
