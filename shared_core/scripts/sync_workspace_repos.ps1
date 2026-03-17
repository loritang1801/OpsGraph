param(
    [string[]]$RepoNames = @("AuditFlow", "OpsGraph"),
    [string]$DestinationName = "shared_core"
)

$ErrorActionPreference = "Stop"

$sharedRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $sharedRoot
$vendorScript = Join-Path $PSScriptRoot "vendor_into_repo.ps1"

if (-not (Test-Path $vendorScript)) {
    throw "Vendor script not found: $vendorScript"
}

$results = @()

foreach ($repoName in $RepoNames) {
    $targetRepoPath = Join-Path $workspaceRoot $repoName
    if (-not (Test-Path $targetRepoPath)) {
        throw "Workspace repo path does not exist: $targetRepoPath"
    }

    Write-Output "Syncing SharedAgentCore into $targetRepoPath"
    & $vendorScript -TargetRepoPath $targetRepoPath -DestinationName $DestinationName

    $results += [pscustomobject]@{
        RepoName = $repoName
        TargetPath = Join-Path $targetRepoPath $DestinationName
    }
}

Write-Output ""
Write-Output "Sync completed for workspace repos:"
$results | Format-Table -AutoSize | Out-String | Write-Output
