!macro artyRemoveNativeHostKey KEY
  ReadRegStr $0 HKCU "${KEY}" ""
  StrLen $1 "$APPDATA\Arty\native-host"
  StrCpy $2 $0 $1
  StrCmp $2 "$APPDATA\Arty\native-host" 0 +2
  DeleteRegKey HKCU "${KEY}"
!macroend

!macro customUnInstall
  !insertmacro artyRemoveNativeHostKey "Software\Google\Chrome\NativeMessagingHosts\app.arty.desktop"
  !insertmacro artyRemoveNativeHostKey "Software\Chromium\NativeMessagingHosts\app.arty.desktop"
  !insertmacro artyRemoveNativeHostKey "Software\Microsoft\Edge\NativeMessagingHosts\app.arty.desktop"
  !insertmacro artyRemoveNativeHostKey "Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\app.arty.desktop"
  !insertmacro artyRemoveNativeHostKey "Software\Mozilla\NativeMessagingHosts\app.arty.desktop"
  RMDir /r "$APPDATA\Arty\native-host"
!macroend
