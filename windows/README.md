# Usage

## flac to opus

Makes a new folder in the parent folder, which is a complete copy of the folder that you execute the script in, except .flac files are replaced with .opus files.

Navigate into the folder that you wish to make a complete copy of, and just run the script. Make sure you have writing rights to the parent folder.

You must install opusenc on your system, and it must be included in the system PATH. I originally tried ffmpeg, but it didn't save over the album art, which is why I chose opusenc.



## youtube dl playlist

Downloads a playlist and renames files according to their position in the playlist, and their title.

Replace your desired playlist into the file before using. If download fails at some point, you can set the position of the failed download and run the command again.