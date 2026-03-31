param(
    [Parameter(Mandatory = $true)]
    [string]$RootDir
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path $RootDir).Path
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$backendUrl = "http://127.0.0.1:8000/api/status"
$frontendUrl = "http://127.0.0.1:5173"
$browserProfileDir = Join-Path $env:TEMP ("manga-translator-browser-" + [guid]::NewGuid().ToString("N"))

$backendProcess = $null
$frontendProcess = $null
$browserProcess = $null

function Wait-HttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 45
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        } catch {
        }
        Start-Sleep -Milliseconds 500
    }

    return $false
}

function Start-CmdWindow {
    param(
        [string]$Title,
        [string]$WorkingDirectory,
        [string]$Command
    )

    $fullCommand = "title $Title && $Command"
    return Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", $fullCommand) -WorkingDirectory $WorkingDirectory -PassThru
}

function Stop-ProcessTree {
    param(
        [System.Diagnostics.Process]$Process
    )

    if ($null -eq $Process) {
        return
    }

    try {
        if (-not $Process.HasExited) {
            & cmd.exe /c "taskkill /PID $($Process.Id) /T /F" | Out-Null
        }
    } catch {
    }
}

function Get-BrowserExecutable {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    return $null
}

try {
    Write-Host ""
    Write-Host "==================================================="
    Write-Host "Managed browser session starting"
    Write-Host "Closing the dedicated browser window will stop"
    Write-Host "both backend and frontend services automatically."
    Write-Host "==================================================="

    $backendProcess = Start-CmdWindow `
        -Title "Manga Translator API" `
        -WorkingDirectory $backendDir `
        -Command "call venv\Scripts\activate.bat && uvicorn main:app --host 0.0.0.0 --port 8000"

    if (-not (Wait-HttpReady -Url $backendUrl -TimeoutSeconds 60)) {
        throw "Backend API did not become ready in time."
    }

    $frontendProcess = Start-CmdWindow `
        -Title "Manga Translator WebUI" `
        -WorkingDirectory $frontendDir `
        -Command "npm run dev"

    if (-not (Wait-HttpReady -Url $frontendUrl -TimeoutSeconds 60)) {
        throw "Frontend WebUI did not become ready in time."
    }

    $browserExe = Get-BrowserExecutable
    if ($browserExe) {
        New-Item -ItemType Directory -Path $browserProfileDir -Force | Out-Null
        $browserArgs = @(
            "--new-window",
            "--app=$frontendUrl",
            "--user-data-dir=$browserProfileDir",
            "--no-first-run",
            "--disable-sync"
        )
        $browserProcess = Start-Process -FilePath $browserExe -ArgumentList $browserArgs -PassThru
        Write-Host ""
        Write-Host "Backend API:  http://localhost:8000"
        Write-Host "Frontend UI:  http://localhost:5173"
        Write-Host "Browser mode: dedicated app window"
        Write-Host ""
        Write-Host "Close that browser window to stop this session."
        Wait-Process -Id $browserProcess.Id
    } else {
        Start-Process $frontendUrl | Out-Null
        Write-Warning "Could not find Edge/Chrome. Opened the default browser, but auto-shutdown on window close is unavailable in this fallback mode."
        Write-Host "Press Ctrl+C in this launcher window to stop backend/frontend."
        while ($true) {
            Start-Sleep -Seconds 1
        }
    }
} finally {
    Write-Host ""
    Write-Host "Shutting down backend/frontend..."
    Stop-ProcessTree -Process $frontendProcess
    Stop-ProcessTree -Process $backendProcess

    if (Test-Path $browserProfileDir) {
        Remove-Item -Path $browserProfileDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
