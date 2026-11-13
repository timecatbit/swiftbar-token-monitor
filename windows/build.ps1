param([string]$ExpectedTag = "")
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Repo
$Entry = Get-Content -LiteralPath "token-monitor.30s.sh" -Raw
if ($Entry -notmatch '<xbar.version>([0-9]+\.[0-9]+\.[0-9]+)</xbar.version>') {
    throw "Cannot read the Token Monitor version."
}
$Version = $Matches[1]
if ($ExpectedTag -and $ExpectedTag -ne "v$Version") { throw "Version does not match the requested tag." }
$Arch = python -c "import platform; print(platform.machine().lower())"
if ($LASTEXITCODE -ne 0) { throw "Python could not run." }
if ($Arch -eq "amd64") { $Arch = "x64" }
if ($Arch -ne "x64") { throw "Use x64 Python for this build." }
$Name = "token-monitor-$Version-windows-$Arch"
$Dist = Join-Path $Repo "dist"
$Build = Join-Path $Repo "build/windows"
New-Item -ItemType Directory -Force -Path $Dist, $Build | Out-Null
# Backslashes throughout: Inno Setup is handed these paths too.
$Dist = [IO.Path]::GetFullPath($Dist); $Build = [IO.Path]::GetFullPath($Build)
$env:PYTHONUTF8 = "1"
# The tray star doubles as the program icon, so the exe, its shortcuts and the uninstall entry show it.
$Icon = Join-Path $Build "token-monitor.ico"
python windows/make_icon.py $Icon
if ($LASTEXITCODE -ne 0) { throw "Could not draw the program icon." }
$BuildArgs = @(
    "--noconfirm", "--clean", "--windowed", "--onedir", "--name", "Token Monitor", "--icon", $Icon,
    "--paths", (Join-Path $Repo "lib"), "--workpath", "$Build/work", "--specpath", "$Build", "--distpath", "$Build/output",
    "--add-data", "$Repo/assets;assets", "--add-data", "$Repo/lib/model-pricing.json;lib",
    "--add-data", "$Repo/lib/codex-pricing.json;lib", "--collect-data", "tzdata",
    "--hidden-import", "zstandard", (Join-Path $Repo "windows/token_monitor.py")
)
python -m PyInstaller @BuildArgs
if ($LASTEXITCODE -ne 0) { throw "Windows build failed." }
$App = Join-Path $Build "output\Token Monitor"
Copy-Item -LiteralPath "LICENSE", "lib/CCUSAGE_LICENSE", "lib/CODEXBAR_LICENSE", "windows/THIRD_PARTY.md" -Destination $App
$Site = python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
$Notice = Join-Path $App "licenses"
New-Item -ItemType Directory -Force -Path $Notice | Out-Null
Get-ChildItem -LiteralPath $Site -Directory -Filter "*.dist-info" | ForEach-Object {
    $Metadata = $_
    $LicenseFiles = @(Get-ChildItem -LiteralPath $Metadata.FullName -File -Filter "LICEN*")
    $LicenseDir = Join-Path $Metadata.FullName "licenses"
    if (Test-Path -LiteralPath $LicenseDir) { $LicenseFiles += Get-ChildItem -LiteralPath $LicenseDir -File -Recurse }
    if ($LicenseFiles.Count) {
        $Dest = Join-Path $Notice $Metadata.Name
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
        $LicenseFiles | ForEach-Object {
            $Relative = $_.FullName.Substring($Metadata.FullName.Length + 1)
            $Target = Join-Path $Dest $Relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $Target
        }
    }
}
# PySide6's Windows wheels carry only Qt's commercial notice, so the LGPL the app uses Qt under, and the
# GPL it builds on, ship from this repository. Python's licence covers the embedded interpreter and the
# libraries its Windows build includes.
Copy-Item -LiteralPath "windows/licenses/LGPL-3.0.txt", "windows/licenses/GPL-3.0.txt" -Destination $Notice
$PythonLicense = Join-Path (python -c "import sys; print(sys.base_prefix)") "LICENSE.txt"
if (-not (Test-Path -LiteralPath $PythonLicense)) { throw "Python's LICENSE.txt was not found." }
Copy-Item -LiteralPath $PythonLicense -Destination (Join-Path $Notice "Python-LICENSE.txt")
$env:QT_QPA_PLATFORM = "offscreen"
# Offscreen Qt reads fonts from a folder rather than asking Windows, and the bundle ships none, so the
# smoke picture showed every character as a box. Point it at the system fonts.
$env:QT_QPA_FONTDIR = Join-Path $env:WINDIR "Fonts"
$Preview = Join-Path $Build "smoke.png"
Remove-Item -LiteralPath $Preview -ErrorAction SilentlyContinue
$Process = Start-Process -FilePath (Join-Path $App "Token Monitor.exe") -ArgumentList @("--self-test", "--preview", ('"' + $Preview + '"')) -Wait -PassThru
if ($Process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $Preview)) { throw "Packaged Windows smoke test failed." }

function Write-Checksum([string]$Path) {
    $Hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $(Split-Path -Leaf $Path)" | Set-Content -LiteralPath "$Path.sha256" -Encoding ascii
}
$Archive = Join-Path $Dist "$Name.zip"
Compress-Archive -LiteralPath $App -DestinationPath $Archive -Force
Write-Checksum $Archive
Write-Output $Archive

# The installer: the same folder for this user only, with a Start menu shortcut and an uninstall entry.
$Iscc = @(
    (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $Iscc) {
    Remove-Item Env:QT_QPA_PLATFORM, Env:QT_QPA_FONTDIR
    if ($env:CI) { throw "Inno Setup 6.5 or later is needed to build the installer." }
    Write-Warning "Inno Setup was not found, so only the ZIP was built."
    return
}
$SetupName = "$Name-setup"
& $Iscc "/DAppVersion=$Version" "/DAppDir=$App" "/DIconFile=$Icon" `
    "/O$Dist" "/F$SetupName" (Join-Path $Repo "windows/installer/token-monitor.iss")
if ($LASTEXITCODE -ne 0) { throw "Installer build failed." }
$Setup = Join-Path $Dist "$SetupName.exe"

# Install into a scratch folder, start the installed copy's smoke test, then uninstall it.
$Installed = Join-Path $Build "installed"
$InstalledExe = Join-Path $Installed "Token Monitor.exe"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "Token Monitor.lnk"
$Entry = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{66818B0F-B59E-45CC-81AA-99315BFB1931}_is1"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
Remove-Item -LiteralPath $Installed -Recurse -Force -ErrorAction SilentlyContinue
$Process = Start-Process -FilePath $Setup -Wait -PassThru -ArgumentList @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=autostart",
    ('/DIR="' + $Installed + '"'), ('/LOG="' + (Join-Path $Build "setup.log") + '"'))
if ($Process.ExitCode -ne 0) { throw "The installer exited with $($Process.ExitCode)." }
if (-not ((Test-Path -LiteralPath $InstalledExe) -and (Test-Path -LiteralPath $Shortcut) -and (Test-Path -LiteralPath $Entry))) {
    throw "The installer left out the program, its Start menu shortcut or its uninstall entry."
}
$SignIn = (Get-ItemProperty -LiteralPath $RunKey -Name "TokenMonitor" -ErrorAction SilentlyContinue).TokenMonitor
if ($SignIn -ne ('"' + $InstalledExe + '" --background')) { throw "The sign-in entry does not start the installed copy." }
$Process = Start-Process -FilePath $InstalledExe -ArgumentList "--self-test" -Wait -PassThru
if ($Process.ExitCode -ne 0) { throw "The installed copy failed its smoke test." }
$Process = Start-Process -FilePath (Join-Path $Installed "unins000.exe") -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -Wait -PassThru
if ($Process.ExitCode -ne 0) { throw "The uninstaller exited with $($Process.ExitCode)." }
# The uninstaller finishes from a copy of itself in the temp folder, after this process has returned.
$Deadline = (Get-Date).AddSeconds(60)
while (((Test-Path -LiteralPath $InstalledExe) -or (Test-Path -LiteralPath $Shortcut) -or (Test-Path -LiteralPath $Entry)) -and (Get-Date) -lt $Deadline) {
    Start-Sleep -Milliseconds 500
}
if ((Test-Path -LiteralPath $InstalledExe) -or (Test-Path -LiteralPath $Shortcut) -or (Test-Path -LiteralPath $Entry)) {
    throw "Uninstalling left the program, its shortcut or its entry behind."
}
if (Get-ItemProperty -LiteralPath $RunKey -Name "TokenMonitor" -ErrorAction SilentlyContinue) { throw "Uninstalling left the sign-in entry behind." }
Remove-Item Env:QT_QPA_PLATFORM, Env:QT_QPA_FONTDIR
Write-Checksum $Setup
Write-Output $Setup
