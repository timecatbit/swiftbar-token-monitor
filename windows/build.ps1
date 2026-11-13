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
$env:PYTHONUTF8 = "1"
$BuildArgs = @(
    "--noconfirm", "--clean", "--windowed", "--onedir", "--name", "Token Monitor",
    "--paths", (Join-Path $Repo "lib"), "--workpath", "$Build/work", "--specpath", "$Build", "--distpath", "$Build/output",
    "--add-data", "$Repo/assets;assets", "--add-data", "$Repo/lib/model-pricing.json;lib",
    "--add-data", "$Repo/lib/codex-pricing.json;lib", "--collect-data", "tzdata",
    "--hidden-import", "zstandard", (Join-Path $Repo "windows/token_monitor.py")
)
python -m PyInstaller @BuildArgs
if ($LASTEXITCODE -ne 0) { throw "Windows build failed." }
$App = Join-Path $Build "output/Token Monitor"
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
$env:QT_QPA_PLATFORM = "offscreen"
# Offscreen Qt reads fonts from a folder rather than asking Windows, and the bundle ships none, so the
# smoke picture showed every character as a box. Point it at the system fonts.
$env:QT_QPA_FONTDIR = Join-Path $env:WINDIR "Fonts"
$Preview = Join-Path $Build "smoke.png"
Remove-Item -LiteralPath $Preview -ErrorAction SilentlyContinue
$Process = Start-Process -FilePath (Join-Path $App "Token Monitor.exe") -ArgumentList @("--self-test", "--preview", ('"' + $Preview + '"')) -Wait -PassThru
Remove-Item Env:QT_QPA_PLATFORM, Env:QT_QPA_FONTDIR
if ($Process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $Preview)) { throw "Packaged Windows smoke test failed." }
$Archive = Join-Path $Dist "$Name.zip"
Compress-Archive -LiteralPath $App -DestinationPath $Archive -Force
$Hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $Name.zip" | Set-Content -LiteralPath "$Archive.sha256" -Encoding ascii
Write-Output $Archive
