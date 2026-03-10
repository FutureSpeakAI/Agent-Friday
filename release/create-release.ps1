Set-Location "C:\Users\user\Projects\nexus-os"

gh release create v3.6.0 `
  --title "Agent Friday v3.6.0 — Local Voice OS" `
  --notes-file "release\release-notes.md" `
  "release\Agent Friday Setup 3.6.0.exe"
