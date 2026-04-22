param(
    [Parameter(Mandatory = $true)]
    [string]$RootDir
)

$ErrorActionPreference = "Stop"

$normalizedRootDir = ($RootDir | ForEach-Object { $_.Trim().Trim('"') }).TrimEnd('\')
if ([string]::IsNullOrWhiteSpace($normalizedRootDir)) {
    throw "RootDir is empty after normalization."
}

$root = (Resolve-Path -LiteralPath $normalizedRootDir).Path
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$backendUrl = "http://127.0.0.1:8000/api/status"
$frontendUrl = "http://127.0.0.1:5173"
$browserProfileBase = if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    Join-Path $env:LOCALAPPDATA "MangaTranslator"
} else {
    Join-Path $root ".runtime"
}
$browserProfileDir = Join-Path $browserProfileBase "browser-profile"
$logDir = Join-Path $browserProfileBase "logs"
$backendLogPath = Join-Path $logDir "backend-managed.log"
$frontendLogPath = Join-Path $logDir "frontend-managed.log"

$backendProcess = $null
$frontendProcess = $null
$browserProcess = $null

function Wait-HttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 45,
        [System.Diagnostics.Process]$Process = $null
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

        if ($null -ne $Process) {
            try {
                if ($Process.HasExited) {
                    return $false
                }
            } catch {
            }
        }

        Start-Sleep -Milliseconds 500
    }

    return $false
}

function Get-LogTail {
    param(
        [string]$Path,
        [int]$LineCount = 80
    )

    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return "(log file not found: $Path)"
    }

    try {
        return (Get-Content -LiteralPath $Path -Tail $LineCount -ErrorAction Stop) -join [Environment]::NewLine
    } catch {
        return "(failed to read log file: $Path)"
    }
}

function New-StartupFailureMessage {
    param(
        [string]$ServiceName,
        [string]$Url,
        [System.Diagnostics.Process]$Process,
        [string]$LogPath
    )

    $processState = "unknown"
    if ($null -ne $Process) {
        try {
            $processState = if ($Process.HasExited) {
                "exited with code $($Process.ExitCode)"
            } else {
                "still running, PID $($Process.Id)"
            }
        } catch {
            $processState = "unavailable"
        }
    }

    $tail = Get-LogTail -Path $LogPath
    return @"
$ServiceName did not become ready in time.
Checked URL: $Url
Process state: $processState
Log file: $LogPath

Last log lines:
$tail
"@
}

function Start-CmdWindow {
    param(
        [string]$Title,
        [string]$WorkingDirectory,
        [string]$Command,
        [string]$LogPath
    )

    $safeTitle = $Title.Replace('"', "'")
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $quotedLogPath = '"' + $LogPath + '"'
    $fullCommand = 'title "' + $safeTitle + '" && echo [' + $timestamp + '] Starting ' + $safeTitle + ' > ' + $quotedLogPath + ' && ' + $Command + ' 1>> ' + $quotedLogPath + ' 2>&1'
    return Start-Process -FilePath "cmd.exe" -ArgumentList @("/d", "/c", $fullCommand) -WorkingDirectory $WorkingDirectory -PassThru
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

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null

    $backendProcess = Start-CmdWindow `
        -Title "Manga Translator API" `
        -WorkingDirectory $backendDir `
        -Command 'call venv\Scripts\activate.bat && python -m uvicorn main:app --host 127.0.0.1 --port 8000' `
        -LogPath $backendLogPath

    if (-not (Wait-HttpReady -Url $backendUrl -TimeoutSeconds 90 -Process $backendProcess)) {
        throw (New-StartupFailureMessage -ServiceName "Backend API" -Url $backendUrl -Process $backendProcess -LogPath $backendLogPath)
    }

    $frontendProcess = Start-CmdWindow `
        -Title "Manga Translator WebUI" `
        -WorkingDirectory $frontendDir `
        -Command 'set "VITE_DEV_PROXY_TARGET=http://127.0.0.1:8000" && set "VITE_API_BASE_URL=http://127.0.0.1:8000" && npm run dev -- --host 127.0.0.1 --port 5173 --strictPort' `
        -LogPath $frontendLogPath

    if (-not (Wait-HttpReady -Url $frontendUrl -TimeoutSeconds 120 -Process $frontendProcess)) {
        throw (New-StartupFailureMessage -ServiceName "Frontend WebUI" -Url $frontendUrl -Process $frontendProcess -LogPath $frontendLogPath)
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
        Write-Host "Logs:          $logDir"
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

}
