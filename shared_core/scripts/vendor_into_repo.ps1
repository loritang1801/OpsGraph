param(
    [Parameter(Mandatory = $true)]
    [string]$TargetRepoPath,

    [string]$DestinationName = "shared_core"
)

$sourceRoot = Split-Path -Parent $PSScriptRoot
$destinationRoot = Join-Path $TargetRepoPath $DestinationName
$excludedRootNames = @(".git", "__pycache__", ".pytest_cache", ".venv", "dist", "build")
$excludedDirNames = @("__pycache__", ".pytest_cache", ".venv", "dist", "build")

if (-not (Test-Path $TargetRepoPath)) {
    throw "Target repo path does not exist: $TargetRepoPath"
}

if (Test-Path $destinationRoot) {
    Remove-Item -Recurse -Force $destinationRoot
}

New-Item -ItemType Directory -Path $destinationRoot | Out-Null

Get-ChildItem -Force $sourceRoot |
    Where-Object { $_.Name -notin $excludedRootNames } |
    ForEach-Object {
        Copy-Item -Recurse -Force $_.FullName $destinationRoot
    }

Get-ChildItem -Directory -Recurse -Force $destinationRoot |
    Where-Object { $_.Name -in $excludedDirNames } |
    ForEach-Object {
        Remove-Item -Recurse -Force $_.FullName
    }

Get-ChildItem -Recurse -Force -File $destinationRoot |
    Where-Object { $_.Extension -eq ".pyc" } |
    ForEach-Object {
        Remove-Item -Force $_.FullName
    }

Write-Output "Vendored SharedAgentCore into $destinationRoot"
