<#
  run_folder.ps1
  --------------
  Prompt for a folder of transparent 4000x4000 PNGs and a folder for JPG output,
  then run process_bottle.jsx over every PNG back to back through Photoshop COM.

  For each PNG, the JSX:
    - resizes the visible layer so its longest edge is 3600 px
    - centers the layer on the 4000x4000 canvas
    - saves the transparent PNG in place
    - adds a white background
    - saves a JPG copy to the selected JPG folder

  Usage:
    .\run_folder.ps1
    .\run_folder.ps1 -PngFolder "G:\path\to\pngs" -JpgFolder "G:\path\to\jpgs"
    .\run_folder.ps1 -PngFolder "G:\path\to\pngs" -JpgFolder "G:\path\to\jpgs" -Recurse

  Requires: Photoshop installed (COM). Run PowerShell at the same privilege
  level as Photoshop (both normal, or both admin).
#>
[CmdletBinding()]
param(
    [string]$PngFolder,
    [string]$JpgFolder,
    [string]$Jsx,
    [switch]$Recurse
)

$ErrorActionPreference = "Stop"

function Get-CurrentScriptDirectory {
    $dir = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($dir) -and $PSCommandPath) {
        $dir = Split-Path -Parent $PSCommandPath
    }
    if ([string]::IsNullOrWhiteSpace($dir) -and $MyInvocation.ScriptName) {
        $dir = Split-Path -Parent $MyInvocation.ScriptName
    }
    return $dir
}

function Read-TerminalPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt
    )

    Write-Host -NoNewline $Prompt
    $value = [Console]::ReadLine()
    if ($null -eq $value) { return $null }

    $value = $value.Trim()
    if ($value.Length -ge 2) {
        $first = $value.Substring(0, 1)
        $last = $value.Substring($value.Length - 1, 1)

        if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }
    }

    return $value
}

function ConvertTo-JsStringContent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    return $Value.Replace('\', '/').
        Replace('"', '\"').
        Replace("`r", '\r').
        Replace("`n", '\n')
}

function Read-ContinueChoice {
    while ($true) {
        Write-Host -NoNewline "Do you want to continue or close? [Y/N]: "
        $choice = [Console]::ReadLine()
        if ($null -eq $choice) { return $false }

        $choice = $choice.Trim()
        if ($choice -ieq "Y" -or $choice -ieq "YES") { return $true }
        if ($choice -ieq "N" -or $choice -ieq "NO") { return $false }

        Write-Host "Please enter Y or N."
    }
}

function Invoke-BottleFolderProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PngFolder,

        [Parameter(Mandatory = $true)]
        [string]$JpgFolder,

        [Parameter(Mandatory = $true)]
        [string]$JsxTemplate,

        [switch]$Recurse
    )

    if (-not (Test-Path -LiteralPath $PngFolder)) { throw "PNG folder not found: $PngFolder" }
    if (-not (Get-Item -LiteralPath $PngFolder).PSIsContainer) { throw "PNG path is not a folder: $PngFolder" }

    if (Test-Path -LiteralPath $JpgFolder) {
        if (-not (Get-Item -LiteralPath $JpgFolder).PSIsContainer) {
            throw "JPG output path is not a folder: $JpgFolder"
        }
    } else {
        [void](New-Item -ItemType Directory -Path $JpgFolder)
    }

    $PngFolder = (Resolve-Path -LiteralPath $PngFolder).ProviderPath
    $JpgFolder = (Resolve-Path -LiteralPath $JpgFolder).ProviderPath

    $pngs = @(Get-ChildItem -LiteralPath $PngFolder -Filter *.png -File -Recurse:$Recurse | Sort-Object FullName)
    if ($pngs.Count -eq 0) {
        Write-Host "No PNG files found in $PngFolder"
        return
    }

    Write-Host ("PNG folder : {0}" -f $PngFolder)
    Write-Host ("JPG folder : {0}" -f $JpgFolder)
    Write-Host ("Mode       : {0}" -f $(if ($Recurse) { "recursive" } else { "selected folder only" }))
    Write-Host ("Found {0} PNG(s) to process." -f $pngs.Count)

    Write-Host "Starting Photoshop (COM)..."
    $ps = $null
    try {
        try {
            $ps = New-Object -ComObject Photoshop.Application
        } catch {
            throw "Could not start Photoshop via COM. Is Photoshop installed? $($_.Exception.Message)"
        }

        $ps.DisplayDialogs = 3 # 3 = psDisplayNoDialogs

        $miss = [System.Reflection.Missing]::Value
        $ok = 0
        $fail = 0
        $failed = @()
        $jpgFolderForJs = ConvertTo-JsStringContent -Value $JpgFolder

        for ($i = 0; $i -lt $pngs.Count; $i++) {
            $png = $pngs[$i]
            $tag = "[{0}/{1}] {2}" -f ($i + 1), $pngs.Count, $png.Name

            try {
                $pngPathForJs = ConvertTo-JsStringContent -Value $png.FullName
                $script = $JsxTemplate.
                    Replace("TARGET_PNG_PLACEHOLDER", $pngPathForJs).
                    Replace("TARGET_JPG_FOLDER_PLACEHOLDER", $jpgFolderForJs)

                $null = $ps.DoJavaScript($script, $miss, 1) # 1 = psNeverShowDebugger
                Write-Host "$tag  OK"
                $ok++
            } catch {
                Write-Warning "$tag  FAILED: $($_.Exception.Message)"
                $failed += $png.FullName
                $fail++
            }
        }

        Write-Host ""
        Write-Host ("Done. {0} OK, {1} failed, {2} total." -f $ok, $fail, $pngs.Count)
        if ($failed.Count -gt 0) {
            Write-Host "Failed files:"
            $failed | ForEach-Object { Write-Host "  $_" }
        }
    } finally {
        if ($ps) {
            [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($ps)
            [GC]::Collect()
            [GC]::WaitForPendingFinalizers()
        }
    }
}

$scriptDir = Get-CurrentScriptDirectory
if ([string]::IsNullOrWhiteSpace($Jsx)) {
    $Jsx = Join-Path $scriptDir "process_bottle.jsx"
}

if (-not (Test-Path -LiteralPath $Jsx)) { throw "JSX not found: $Jsx" }
$Jsx = (Resolve-Path -LiteralPath $Jsx).ProviderPath

$jsxTemplate = Get-Content -LiteralPath $Jsx -Raw
$requiredTokens = @(
    "TARGET_PNG_PLACEHOLDER",
    "TARGET_JPG_FOLDER_PLACEHOLDER"
)
foreach ($token in $requiredTokens) {
    if (-not $jsxTemplate.Contains($token)) {
        throw "JSX is missing the $token token; cannot inject paths."
    }
}

$nextPngFolder = $PngFolder
$nextJpgFolder = $JpgFolder

while ($true) {
    if ([string]::IsNullOrWhiteSpace($nextPngFolder)) {
        $nextPngFolder = Read-TerminalPath -Prompt "PNG folder location: "
        if ([string]::IsNullOrWhiteSpace($nextPngFolder)) {
            Write-Host "Canceled: no PNG folder entered."
            return
        }
    }

    if ([string]::IsNullOrWhiteSpace($nextJpgFolder)) {
        $nextJpgFolder = Read-TerminalPath -Prompt "JPG folder location: "
        if ([string]::IsNullOrWhiteSpace($nextJpgFolder)) {
            Write-Host "Canceled: no JPG output folder entered."
            return
        }
    }

    try {
        Invoke-BottleFolderProcess `
            -PngFolder $nextPngFolder `
            -JpgFolder $nextJpgFolder `
            -JsxTemplate $jsxTemplate `
            -Recurse:$Recurse
    } catch [System.Management.Automation.PipelineStoppedException] {
        throw
    } catch {
        Write-Warning "Batch failed: $($_.Exception.Message)"
    }

    Write-Host ""
    if (-not (Read-ContinueChoice)) {
        return
    }

    Write-Host ""
    $nextPngFolder = $null
    $nextJpgFolder = $null
}
