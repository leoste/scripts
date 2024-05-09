$parent = (Get-Location).Path + " (opus)";
Get-ChildItem -Recurse *.flac | foreach-object {	
	$child = ($_ | Resolve-Path -Relative).subString(1);
	$name = $parent + $child.Remove($child.Length - $_.Extension.Length) + ".opus";		
	if (Test-Path -PathType Leaf -Path $name) {
		Write-Host "ERROR: [$name] already exists.";
	}
	else {
		New-Item -ItemType File -Path $name -Force;
		opusenc --bitrate 256 "$_" "$name" --quiet;
	}	
}
