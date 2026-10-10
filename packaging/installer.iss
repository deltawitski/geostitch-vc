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
ChangesAssociations=yes

[Files]
Source: "..\dist\GeoStitch\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Registry]
Root: HKA; Subkey: "Software\Classes\.gsp"; ValueType: string; ValueName: ""; ValueData: "GeoStitch.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\GeoStitch.Project"; ValueType: string; ValueName: ""; ValueData: "GeoStitch project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\GeoStitch.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\GeoStitch.exe"",0"
Root: HKA; Subkey: "Software\Classes\GeoStitch.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\GeoStitch.exe"" ""%1"""

[Icons]
Name: "{group}\GeoStitch"; Filename: "{app}\GeoStitch.exe"
Name: "{autodesktop}\GeoStitch"; Filename: "{app}\GeoStitch.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"

[Run]
Filename: "{app}\GeoStitch.exe"; Flags: postinstall nowait skipifsilent
