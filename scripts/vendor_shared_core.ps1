$source = Resolve-Path (Join-Path $PSScriptRoot "..\\..\\SharedAgentCore")
$target = Resolve-Path (Join-Path $PSScriptRoot "..")
$script = Join-Path $source "scripts\\vendor_into_repo.ps1"

& $script -TargetRepoPath $target -DestinationName "shared_core"
