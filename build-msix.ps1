param(
  [string]$PackageName = "williswyf.EyeBreakTimer",
  [string]$Publisher = "CN=willis",
  [string]$PublisherDisplayName = "willis",
  [string]$PackageVersion = "1.0.0.0"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExePath = Join-Path $Root "dist\Eye Break Timer.exe"
$PackageRoot = Join-Path $Root "msix-package"
$AssetsDir = Join-Path $PackageRoot "Assets"
$OutDir = Join-Path $Root "store-package"
$OutPackage = Join-Path $OutDir "EyeBreakTimer_${PackageVersion}_x64.msix"
$OutUpload = Join-Path $OutDir "EyeBreakTimer_${PackageVersion}_x64.msixupload"
$OutUploadZip = Join-Path $OutDir "EyeBreakTimer_${PackageVersion}_x64.zip"
$MakeAppx = "C:\Program Files (x86)\Windows Kits\10\bin\10.0.18362.0\x64\makeappx.exe"

if (!(Test-Path -LiteralPath $ExePath)) {
  throw "Missing EXE. Run: python -m PyInstaller --noconfirm --noconsole --onefile --name `"Eye Break Timer`" --add-data `"assets\E.png;assets`" rest_reminder.py"
}

if (!(Test-Path -LiteralPath $MakeAppx)) {
  throw "Missing makeappx.exe. Install Windows SDK first."
}

Remove-Item -LiteralPath $PackageRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $AssetsDir, $OutDir | Out-Null
Copy-Item -LiteralPath $ExePath -Destination (Join-Path $PackageRoot "Eye Break Timer.exe") -Force

$assetScript = @'
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import sys

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

def logo(size, path):
    img = Image.new("RGBA", (size, size), "#101214")
    draw = ImageDraw.Draw(img)
    margin = max(4, size // 10)
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=max(4, size // 12),
        outline="#42d392",
        width=max(2, size // 18),
    )
    text = "E"
    try:
        font = ImageFont.truetype("arialbd.ttf", int(size * 0.56))
    except Exception:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2 - size * 0.04),
        text,
        fill="#f4f7f8",
        font=font,
    )
    img.save(out / path)

logo(44, "Square44x44Logo.png")
logo(150, "Square150x150Logo.png")
logo(50, "StoreLogo.png")
'@

$assetScriptPath = Join-Path $PackageRoot "make_assets.py"
Set-Content -LiteralPath $assetScriptPath -Value $assetScript -Encoding UTF8
python $assetScriptPath $AssetsDir
Remove-Item -LiteralPath $assetScriptPath -Force

$manifest = @"
<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap rescap">

  <Identity
    Name="$PackageName"
    Publisher="$Publisher"
    Version="$PackageVersion"
    ProcessorArchitecture="x64" />

  <Properties>
    <DisplayName>Eye Break Timer</DisplayName>
    <PublisherDisplayName>$PublisherDisplayName</PublisherDisplayName>
    <Logo>Assets\StoreLogo.png</Logo>
  </Properties>

  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26200.0" />
  </Dependencies>

  <Resources>
    <Resource Language="en-us" />
  </Resources>

  <Applications>
    <Application Id="App" Executable="Eye Break Timer.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements
        DisplayName="Eye Break Timer"
        Description="A Windows break reminder that helps you rest your eyes on schedule."
        BackgroundColor="#101214"
        Square150x150Logo="Assets\Square150x150Logo.png"
        Square44x44Logo="Assets\Square44x44Logo.png" />
    </Application>
  </Applications>

  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"@

Set-Content -LiteralPath (Join-Path $PackageRoot "AppxManifest.xml") -Value $manifest -Encoding UTF8

Remove-Item -LiteralPath $OutPackage -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $OutUpload -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $OutUploadZip -Force -ErrorAction SilentlyContinue
& $MakeAppx pack /d $PackageRoot /p $OutPackage /o

Compress-Archive -LiteralPath $OutPackage -DestinationPath $OutUploadZip -Force
Move-Item -LiteralPath $OutUploadZip -Destination $OutUpload -Force

Write-Host "Built: $OutPackage"
Write-Host "Built: $OutUpload"
Write-Host "Important: before Store upload, rebuild with the PackageName and Publisher values from Partner Center."
