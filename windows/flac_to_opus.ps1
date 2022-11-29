$parent = (Get-Location).Path + " (opus)";
Get-ChildItem -Recurse *.flac | foreach-object {    
    $child = ($_ | Resolve-Path -Relative).subString(1);
    $name = $parent + $child.Remove($child.Length - $_.Extension.Length) + ".opus";        
    New-Item -ItemType File -Path $name -Force;
    opusenc --bitrate 160 "$_" "$name" --quiet;    
}