param(
    [string]$Python = "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe",
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$Runtime = "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies"
$PythonRoot = Split-Path -Parent $Python
$Node = Join-Path $Runtime "node\bin\node.exe"
$ArtifactTool = Join-Path $Runtime "node\node_modules\@oai\artifact-tool"
$env:TCL_LIBRARY = Join-Path $PythonRoot "tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $PythonRoot "tcl\tk8.6"
$IconPng = Join-Path $Root "assets\DART-QoE.png"
$IconIco = Join-Path $Root "assets\DART-QoE.ico"
$VersionFile = Join-Path $Root "assets\DART-QoE.version.txt"
$Build = Join-Path $Root "build\pyinstaller"
$Dist = Join-Path $Root "dist"
$TargetName = "DART-QoE"
$WindowMode = if ($Console) { "--console" } else { "--windowed" }
$Output = Join-Path $Root "$TargetName.exe"

$TkinterPackage = Join-Path $PythonRoot "Lib\tkinter"
$TkinterBinary = Join-Path $PythonRoot "DLLs\_tkinter.pyd"
$TclBinary = Join-Path $PythonRoot "DLLs\tcl86t.dll"
$TkBinary = Join-Path $PythonRoot "DLLs\tk86t.dll"
$TclData = Join-Path $PythonRoot "tcl\tcl8.6"
$TkData = Join-Path $PythonRoot "tcl\tk8.6"

foreach ($Path in @(
    $Python, $Node, $ArtifactTool, $VersionFile,
    (Join-Path $Root "export_workbook.mjs"),
    $TkinterPackage, $TkinterBinary, $TclBinary, $TkBinary, $TclData, $TkData
)) {
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
    --version-file $VersionFile `
    --distpath $Dist `
    --workpath $Build `
    --specpath $Build `
    --add-data "$(Join-Path $Root 'export_workbook.mjs');." `
    --add-data "$IconPng;assets" `
    --add-binary "$Node;node" `
    --add-data "$ArtifactTool;node_modules\@oai\artifact-tool" `
    --add-data "$TkinterPackage;tkinter" `
    --add-binary "$TkinterBinary;." `
    --add-binary "$TclBinary;." `
    --add-binary "$TkBinary;." `
    --add-data "$TclData;_tcl_data" `
    --add-data "$TkData;_tk_data" `
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
