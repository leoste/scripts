Get-ChildItem -Directory | ForEach-Object {
    Set-Location $_.FullName
    git pull
    Set-Location ..
}