#define AppVersion "2.0.0"
[Setup]
AppId={{F771AB9B-2DAA-4F33-BCB4-F6317EE1AE9A}
AppName=Simply Capture
AppVersion={#AppVersion}
AppPublisher=Simply Capture
DefaultDirName={localappdata}\Programs\Simply Capture
DefaultGroupName=Simply Capture
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=SimplyCapture-2.0.0-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\SimplyCapture.exe
LicenseFile=LICENSE
WizardStyle=modern
WizardSmallImageFile=assets\installer-icon.bmp
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
VersionInfoVersion=2.0.0.0

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "dist\SimplyCapture.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\third_party_licenses\*"; DestDir: "{app}\third_party_licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Simply Capture"; Filename: "{app}\SimplyCapture.exe"
Name: "{autodesktop}\Simply Capture"; Filename: "{app}\SimplyCapture.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SimplyCapture.exe"; Description: "Launch Simply Capture"; Flags: nowait postinstall skipifsilent
