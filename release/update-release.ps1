Set-Location "C:\Users\swebs\Projects\nexus-os"

Write-Host "=== Deleting old release ===" -ForegroundColor Cyan
gh release delete v3.6.0 --yes --cleanup-tag 2>$null
Write-Host "Old release removed" -ForegroundColor Green

Write-Host ""
Write-Host "=== Recreating tag ===" -ForegroundColor Cyan
git tag -f v3.6.0
git push origin v3.6.0 --force
Write-Host "Tag set" -ForegroundColor Green

Write-Host ""
Write-Host "=== Creating new release with updated installer ===" -ForegroundColor Cyan
gh release create v3.6.0 `
  --title "Agent Friday v3.6.0 — Local Voice OS" `
  --notes-file "release\release-notes.md" `
  "release\Agent Friday Setup 3.6.0.exe"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Release creation failed!" -ForegroundColor Red
    exit 1
}
Write-Host "Release created successfully" -ForegroundColor Green
