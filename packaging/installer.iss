#define AppVersion "0.1.0"

[Setup]
AppName=GeoStitch
AppVersion={#AppVersion}
DefaultDirName={autopf}\GeoStitch
DefaultGroupName=GeoStitch
OutputDir=..\dist
OutputBaseFilename=GeoStitch-Setup-{#AppVersion}
SetupIconFile=..\src\geostitch\assets\app_icon.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\GeoStitch\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\GeoStitch"; Filename: "{app}\GeoStitch.exe"
Name: "{autodesktop}\GeoStitch"; Filename: "{app}\GeoStitch.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"

[Run]
Filename: "{app}\GeoStitch.exe"; Flags: postinstall nowait skipifsilent
