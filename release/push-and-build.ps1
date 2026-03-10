Set-Location "C:\Users\swebs\Projects\nexus-os"

Write-Host "=== Pushing to GitHub ===" -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "Push failed!" -ForegroundColor Red
    exit 1
}
Write-Host "Push successful" -ForegroundColor Green

Write-Host ""
Write-Host "=== Creating v3.6.0 tag ===" -ForegroundColor Cyan
git tag v3.6.0
git push origin v3.6.0
Write-Host "Tag pushed" -ForegroundColor Green

Write-Host ""
Write-Host "=== Building installer ===" -ForegroundColor Cyan
npx electron-builder --win nsis --x64
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed!" -ForegroundColor Red
    exit 1
}
Write-Host "Build successful" -ForegroundColor Green
