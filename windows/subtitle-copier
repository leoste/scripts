# Subtitle copier

$base = Get-Location
$subsRoot = Join-Path $base "Subs"

# Loop over each video file in the base folder
Get-ChildItem -Path $base -Filter *.mp4 | ForEach-Object {
    $video = $_
    $videoName = [System.IO.Path]::GetFileNameWithoutExtension($video.Name)

    # Path to corresponding subtitle folder
    $subFolder = Join-Path $subsRoot $videoName

    if (Test-Path $subFolder) {
        # Pick first .srt file (adjust filter if needed, e.g. prefer English)
        $subFile = Get-ChildItem -Path $subFolder -Filter *.srt | Select-Object -First 1
        if ($subFile) {
            $targetPath = Join-Path $base "$videoName.srt"
            Copy-Item -Path $subFile.FullName -Destination $targetPath -Force
            Write-Output "Copied subtitle for $videoName"
        } else {
            Write-Output "No .srt found in $subFolder"
        }
    } else {
        Write-Output "No subtitle folder found for $videoName"
    }
}
