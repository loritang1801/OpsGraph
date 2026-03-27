param(
    [string[]]$RepoNames = @("AuditFlow", "OpsGraph"),
    [string]$DestinationName = "shared_core"
)

$ErrorActionPreference = "Stop"

$sharedRoot = Split-Path -Parent $PSScriptRoot
$vendorScript = Join-Path $PSScriptRoot "vendor_into_repo.ps1"
$workspaceCandidates = @(
    (Split-Path -Parent $sharedRoot),
    (Split-Path -Parent (Split-Path -Parent $sharedRoot))
) | Where-Object { $_ }

if (-not (Test-Path $vendorScript)) {
    throw "Vendor script not found: $vendorScript"
}

$workspaceRoot = $null
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
foreach ($candidate in $workspaceCandidates) {
    $hasAllRepos = $true
    foreach ($repoName in $RepoNames) {
        if (-not (Test-Path (Join-Path $candidate $repoName))) {
            $hasAllRepos = $false
            break
        }
    }
    if ($hasAllRepos) {
        $workspaceRoot = $candidate
        break
    }
}

if ($null -eq $workspaceRoot) {
    throw (
        "Could not locate a workspace root containing repo(s): " +
        ($RepoNames -join ", ") +
        ". Run this script from the multi-repo workspace or pass repo names that exist beside the shared core."
    )
}

$sourceRoot = $sharedRoot
$workspaceSharedRoot = Join-Path $workspaceRoot "SharedAgentCore"
if (Test-Path $workspaceSharedRoot) {
    $sourceRoot = $workspaceSharedRoot
}

$tempVendorScript = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("vendor_into_repo-" + [System.Guid]::NewGuid().ToString("N") + ".ps1")
$tempSourceRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("shared-core-source-" + [System.Guid]::NewGuid().ToString("N"))

Copy-Item -Force $vendorScript $tempVendorScript
New-Item -ItemType Directory -Path $tempSourceRoot | Out-Null
Get-ChildItem -Force $sourceRoot | ForEach-Object {
    Copy-Item -Recurse -Force $_.FullName $tempSourceRoot
}

$results = @()

try {
    foreach ($repoName in $RepoNames) {
        $targetRepoPath = Join-Path $workspaceRoot $repoName
        if (-not (Test-Path $targetRepoPath)) {
            throw "Workspace repo path does not exist: $targetRepoPath"
        }

        Write-Output "Syncing SharedAgentCore into $targetRepoPath"
        & $tempVendorScript `
            -SourceRoot $tempSourceRoot `
            -TargetRepoPath $targetRepoPath `
            -DestinationName $DestinationName

        $renderWorkflowScript = Join-Path $targetRepoPath "scripts\render_ci_workflow.py"
        if ((Test-Path $renderWorkflowScript) -and $null -ne $pythonCommand) {
            Write-Output "Rendering CI workflow in $targetRepoPath"
            & $pythonCommand.Source $renderWorkflowScript
        }

        $results += [pscustomobject]@{
            RepoName = $repoName
            TargetPath = Join-Path $targetRepoPath $DestinationName
        }
    }
}
finally {
    if (Test-Path $tempVendorScript) {
        Remove-Item -Force $tempVendorScript -ErrorAction SilentlyContinue
    }
    if (Test-Path $tempSourceRoot) {
        Remove-Item -Recurse -Force $tempSourceRoot -ErrorAction SilentlyContinue
    }
}

Write-Output ""
Write-Output "Sync completed for workspace repos:"
$results | Format-Table -AutoSize | Out-String | Write-Output
