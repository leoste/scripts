Get-ChildItem -Directory | ForEach-Object {
    Set-Location $_.FullName
    git add .
    git commit -m "automated commit"
    git push
    Set-Location ..
}