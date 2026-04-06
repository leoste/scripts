for %%a in ("*.flac") do ffmpeg -i "%%a" -ab 320k -map_metadata 0 -id3v2_version 3 "%%~na.mp3"
pause