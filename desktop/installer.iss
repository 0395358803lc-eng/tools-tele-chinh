#define MyAppName "Multi TG Manager"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\\dist-desktop\\installer\\MultiTGManager"
#endif
#ifndef OutputDir
  #define OutputDir "..\\release-desktop"
#endif

[Setup]
AppId={{A48B4D28-9DD3-4A64-B1A9-4CE97B106C9D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Multi TG Manager
DefaultDirName={localappdata}\\Programs\\MultiTGManager
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=MultiTGManager-Setup-{#MyAppVersion}-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\\MultiTGManager.exe
SetupLogging=yes

[Files]
Source: "{#SourceDir}\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\\{#MyAppName}"; Filename: "{app}\\MultiTGManager.exe"
Name: "{autodesktop}\\{#MyAppName}"; Filename: "{app}\\MultiTGManager.exe"

[Run]
Filename: "{app}\\MultiTGManager.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
