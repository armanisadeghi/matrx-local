; AI Matrx Home Connection — Windows installer (NSIS).
;
; NSIS rather than Inno Setup because this repo already speaks NSIS: the desktop app's Tauri
; bundle uses it (desktop/src-tauri/nsis/installer-hooks.nsh). One installer toolchain, not two.
;
; Per-USER, never per-machine (`RequestExecutionLevel user`): the helper lends ONE person's
; internet connection and stores their credential in THEIR Credential Manager. An all-users
; install would put it in a place no one person owns, and its Run key belongs under HKCU anyway.
;
; UNPROVEN BY EXECUTION: this machine is a Mac. Written against the NSIS 3 documentation; the CI
; job builds it, which is the first thing that will say whether it compiles.

Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef VERSION
  !define VERSION "0.1.0"
!endif
!ifndef BINARY
  !define BINARY "matrx-egress.exe"
!endif

Name "AI Matrx Home Connection"
OutFile "AI-Matrx-Home-Connection-windows-setup.exe"
InstallDir "$LOCALAPPDATA\AI Matrx\Home Connection"
InstallDirRegKey HKCU "Software\AI Matrx\Home Connection" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "AI Matrx Home Connection"
VIAddVersionKey "CompanyName" "AI Matrx"
VIAddVersionKey "FileDescription" "Lends this computer's internet connection to your own AI Matrx work"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "AI Matrx"

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\matrx-egress.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Connect this computer to AI Matrx now"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "AI Matrx Home Connection" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /oname=matrx-egress.exe "${BINARY}"

  WriteRegStr HKCU "Software\AI Matrx\Home Connection" "InstallDir" "$INSTDIR"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Add/Remove Programs, under this user only.
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIMatrxHomeConnection" \
    "DisplayName" "AI Matrx Home Connection"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIMatrxHomeConnection" \
    "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIMatrxHomeConnection" \
    "Publisher" "AI Matrx"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIMatrxHomeConnection" \
    "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIMatrxHomeConnection" \
    "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIMatrxHomeConnection" \
    "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIMatrxHomeConnection" \
    "NoRepair" 1

  CreateShortCut "$SMPROGRAMS\AI Matrx Home Connection.lnk" "$INSTDIR\matrx-egress.exe"

  ; The helper writes its own start-up entry, so the registry value has exactly one author.
  ; nsExec::ExecToLog returns the exit code on the stack; a failure is reported, never swallowed.
  nsExec::ExecToLog '"$INSTDIR\matrx-egress.exe" install'
  Pop $0
  ${If} $0 != 0
    DetailPrint "AI Matrx Home Connection was installed but could not set itself to start when \
      you sign in. Open it from the Start menu once and it will."
  ${EndIf}
SectionEnd

Section "Uninstall"
  ; Ask the helper to undo what it registered, then stop it, then remove the files.
  nsExec::ExecToLog '"$INSTDIR\matrx-egress.exe" uninstall'
  Pop $0
  nsExec::ExecToLog 'taskkill /IM matrx-egress.exe /F'
  Pop $0

  Delete "$SMPROGRAMS\AI Matrx Home Connection.lnk"
  Delete "$INSTDIR\matrx-egress.exe"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"

  DeleteRegKey HKCU "Software\AI Matrx\Home Connection"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIMatrxHomeConnection"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "AI Matrx Home Connection"

  ; The Credential Manager item is NOT removed here. It is the person's credential for their
  ; account, and `matrx-egress sign-out` is the one place that removes a computer from an account —
  ; uninstalling the program is not the same decision as leaving the account.
SectionEnd
