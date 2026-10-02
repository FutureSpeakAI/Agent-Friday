'use strict';
/** Remove the scratch server's temporary FRIDAY_HOME after a run. Only a
 *  friday-pw-home-* directory under the system temp folder is ever removed. */
const fs = require('fs');
const os = require('os');
const path = require('path');

module.exports = async function teardown() {
  const home = process.env.FRIDAY_PW_HOME;
  if (!home) return;
  const resolved = path.resolve(home);
  const tmp = path.resolve(os.tmpdir());
  if (path.dirname(resolved) !== tmp || !path.basename(resolved).startsWith('friday-pw-home-')) return;
  try {
    fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 5, retryDelay: 500 });
  } catch (e) {
    console.warn('could not remove the scratch home ' + resolved + ': ' + e.message);
  }
};
