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

  // Write a temp .ps1 script so $ vars are preserved
  const tempPs1 = path.join(os.tmpdir(), `sign-${Date.now()}.ps1`);

  const script = `
$cert = Get-ChildItem -Path "Cert:\\CurrentUser\\My\\${thumbprint}"
if (-not $cert) {
  Write-Error "Certificate not found: ${thumbprint}"
  exit 1
}
try {
  $result = Set-AuthenticodeSignature -FilePath "${filePath.replace(/\\/g, '\\\\')}" -Certificate $cert -HashAlgorithm SHA256 -TimestampServer "http://timestamp.digicert.com"
  if ($result.Status -ne "Valid") {
    Write-Warning "Signature status: $($result.Status) - $($result.StatusMessage)"
  } else {
    Write-Host "Signed OK: ${basename}"
  }
} catch {
  try {
    $result = Set-AuthenticodeSignature -FilePath "${filePath.replace(/\\/g, '\\\\')}" -Certificate $cert -HashAlgorithm SHA256
    Write-Host "Signed OK (no timestamp): ${basename}"
  } catch {
    Write-Error "Signing failed: $_"
    exit 1
  }
}
`;

  try {
    fs.writeFileSync(tempPs1, script, 'utf8');
    execSync(`powershell -ExecutionPolicy Bypass -File "${tempPs1}"`, {
      stdio: 'inherit',
      timeout: 60000,
    });
  } catch (err) {
    console.warn(`  \u26a0 signing failed for ${basename}: ${err.message}`);
  } finally {
    try { fs.unlinkSync(tempPs1); } catch {}
  }
};
