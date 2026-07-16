param(
    [string]$Python = "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe",
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$Runtime = "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies"
$Node = Join-Path $Runtime "node\bin\node.exe"
$ArtifactTool = Join-Path $Runtime "node\node_modules\@oai\artifact-tool"
$env:TCL_LIBRARY = Join-Path $Runtime "python\tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $Runtime "python\tcl\tk8.6"
$IconPng = Join-Path $Root "assets\DART-QoE.png"
$IconIco = Join-Path $Root "assets\DART-QoE.ico"
$Build = Join-Path $Root "build\pyinstaller"
$Dist = Join-Path $Root "dist"
$TargetName = "DART-QoE"
$WindowMode = if ($Console) { "--console" } else { "--windowed" }
$Output = Join-Path $Root "$TargetName.exe"

foreach ($Path in @($Python, $Node, $ArtifactTool, (Join-Path $Root "export_workbook.mjs"))) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required build dependency not found: $Path"
    }
}

& $Python (Join-Path $Root "scripts\build_icon.py")
if ($LASTEXITCODE -ne 0) {
    throw "Icon build failed with exit code $LASTEXITCODE"
}

New-Item -ItemType Directory -Force -Path $Build, $Dist | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    $WindowMode `
    --name $TargetName `
    --icon $IconIco `
    --distpath $Dist `
    --workpath $Build `
    --specpath $Build `
    --add-data "$(Join-Path $Root 'export_workbook.mjs');." `
    --add-data "$IconPng;assets" `
    --add-binary "$Node;node" `
    --add-data "$ArtifactTool;node_modules\@oai\artifact-tool" `
    (Join-Path $Root "app.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$Built = Join-Path $Dist "$TargetName.exe"
if (-not (Test-Path -LiteralPath $Built)) {
    throw "Executable not found after build: $Built"
}

Copy-Item -LiteralPath $Built -Destination $Output -Force
$Hash = (Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Output "Executable: $Output"
Write-Output "Size: $((Get-Item -LiteralPath $Output).Length) bytes"
Write-Output "SHA256: $Hash"
