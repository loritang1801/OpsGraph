param(
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 8000,
    [string]$DatabaseUrl = ""
)

$ErrorActionPreference = "Stop"

function Resolve-PythonCommand {
    param(
        [string]$Root
    )

    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }

    $pythonCommand = Get-Command python -ErrorAction Stop
    return $pythonCommand.Source
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ((-not (Test-Path ".env")) -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example for local startup."
}

$python = Resolve-PythonCommand -Root $Root
$arguments = @(".\scripts\run_api.py", "--host", $ListenHost, "--port", "$Port")
if ($DatabaseUrl) {
    $arguments += @("--database-url", $DatabaseUrl)
}

Write-Host "Starting OpsGraph on http://$ListenHost`:$Port"
Write-Host "If you are using the default local auth data, sign in with admin@example.com / opsgraph-dev-admin / opsgraph."

& $python @arguments
