// Custom signing script for electron-builder
// Uses a temp PowerShell script to avoid $ escaping issues in execSync.

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

exports.default = async function sign(configuration) {
  const filePath = configuration.path;
  if (!filePath) return;

  const ext = path.extname(filePath).toLowerCase();
  if (ext !== '.exe' && ext !== '.dll') return;

  const thumbprint = process.env.CODE_SIGN_THUMBPRINT || 'F54F612271283991614E63F1133F62917B8885EC';
  const basename = path.basename(filePath);

  console.log(`  \u2022 signing  ${basename}`);

  // Build the PowerShell script — exit 1 on ANY non-Valid result so JS can retry
  const buildScript = (fp) => `
$cert = Get-ChildItem -Path "Cert:\\CurrentUser\\My\\${thumbprint}" -ErrorAction SilentlyContinue
if (-not $cert) {
  Write-Error "Certificate not found: ${thumbprint}"
  exit 1
}
try {
  $result = Set-AuthenticodeSignature -FilePath "${fp.replace(/\\/g, '\\\\')}" -Certificate $cert -HashAlgorithm SHA256 -TimestampServer "http://timestamp.digicert.com"
  if ($result.Status -ne "Valid") {
    Write-Host "Signature status: $($result.Status) - $($result.StatusMessage)"
    exit 1
  } else {
    Write-Host "Signed OK: ${basename}"
    exit 0
  }
} catch {
  try {
    $result = Set-AuthenticodeSignature -FilePath "${fp.replace(/\\/g, '\\\\')}" -Certificate $cert -HashAlgorithm SHA256
    if ($result.Status -ne "Valid") {
      Write-Host "Signature status (no ts): $($result.Status) - $($result.StatusMessage)"
      exit 1
    }
    Write-Host "Signed OK (no timestamp): ${basename}"
    exit 0
  } catch {
    Write-Error "Signing failed: $_"
    exit 1
  }
}
`;

  const tempPs1 = path.join(os.tmpdir(), `sign-${Date.now()}.ps1`);
  const maxRetries = 5;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      fs.writeFileSync(tempPs1, buildScript(filePath), 'utf8');
      execSync(`powershell -ExecutionPolicy Bypass -File "${tempPs1}"`, {
        stdio: 'inherit',
        timeout: 60000,
      });
      break; // exit 0 → signing succeeded
    } catch (err) {
      if (attempt < maxRetries) {
        const delaySec = attempt * 2;
        console.log(`  \u23f3 signing ${basename} failed (attempt ${attempt}/${maxRetries}), retrying in ${delaySec}s...`);
        execSync(`powershell -Command "Start-Sleep -Seconds ${delaySec}"`, { stdio: 'ignore' });
      } else {
        console.warn(`  \u26a0 signing failed for ${basename} after ${maxRetries} attempts: ${err.message}`);
      }
    } finally {
      try { fs.unlinkSync(tempPs1); } catch {}
    }
  }
};
