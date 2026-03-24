; Custom NSIS include for Agent Friday installer
; Installs the code-signing certificate into TrustedPublisher and Root stores
; so that future updates and the uninstaller are not blocked by SmartScreen.

!include "x64.nsh"

; Fix: Windows 11 (build 26200+) can create the installer window behind other
; windows due to focus-stealing prevention. Force the window to the foreground.
!macro customInit
  BringToFront
!macroend

; Called after the main install completes
!macro customInstall
  ; Install the signing certificate to Trusted Publishers store
  ; This requires admin elevation (which NSIS already requests via RequestExecutionLevel)
  DetailPrint "Installing code-signing certificate for SmartScreen trust..."
  nsExec::ExecToLog 'certutil -addstore TrustedPublisher "$INSTDIR\agent-friday-dev.cer"'
  Pop $0
  ${If} $0 == 0
    DetailPrint "Certificate added to Trusted Publishers store."
  ${Else}
    DetailPrint "Note: Could not add certificate to Trusted Publishers (code $0). SmartScreen may prompt on updates."
  ${EndIf}

  nsExec::ExecToLog 'certutil -addstore Root "$INSTDIR\agent-friday-dev.cer"'
  Pop $0
  ${If} $0 == 0
    DetailPrint "Certificate added to Trusted Root CAs store."
  ${Else}
    DetailPrint "Note: Could not add certificate to Root CAs (code $0)."
  ${EndIf}

  ; Clean up the cert file from install dir — no longer needed
  Delete "$INSTDIR\agent-friday-dev.cer"
!macroend

; Called before uninstall starts — remove the cert from stores
!macro customUnInstall
  DetailPrint "Removing code-signing certificate..."
  nsExec::ExecToLog 'certutil -delstore TrustedPublisher "Agent Friday Dev"'
  nsExec::ExecToLog 'certutil -delstore Root "Agent Friday Dev"'
!macroend
