/**
 * Console messages the smoke test does not count as the app's fault (F8).
 *
 * WebGL driver warnings and favicon 404s are noise we do not control. So is net::ERR_NO_BUFFER_SPACE: the browser
 * logs it when the Windows machine it runs on has run out of socket buffers (WSAENOBUFS), which says something
 * about the runner, not about the page. A failed fetch the app caused (ERR_CONNECTION_REFUSED, a 404 or 500,
 * a script error) is NOT in this list and still fails the smoke test.
 */
const MACHINE_NOISE = /WebGL|GL_INVALID|favicon|net::ERR_NO_BUFFER_SPACE/i;

export function isNoise(message: string): boolean {
  return MACHINE_NOISE.test(message);
}
