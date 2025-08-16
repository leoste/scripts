# use case: You're creating a video in Da Vinci Resolve for a song you made in FL Studio, or other DAW that allows exporting midi files, you want to edit the video to be in sync with the beats of the track and you can't be bothered manually tracking every clip.
# To solve this problem, this script generates markers from a midi file.
# The midi file should only have one track with custom desired notes, each note will be converted into a marker.
# In ""

import mido

MIDI_FILE = r"C:\documents\example_midi_file.mid"
COLOR = "Yellow"

# load project
pm = resolve.GetProjectManager()
proj = pm.GetCurrentProject()
tl = proj.GetCurrentTimeline()

# get project framerate
framerate = float(proj.GetSetting("timelineFrameRate"))
print("Project framerate is: {framerate}")

mid = mido.MidiFile(MIDI_FILE)
ticks_per_beat = mid.ticks_per_beat
tempo = 500000
current_time_seconds = 0

merged_msgs = mido.merge_tracks(mid.tracks)

timeline_duration_frames = tl.GetEndFrame() - tl.GetStartFrame() + 1

for msg in merged_msgs:
    # msg.time according to mido documentation is delta time in midi files
    current_time_seconds += mido.tick2second(msg.time, ticks_per_beat, tempo)
    
    if msg.type == 'set_tempo':
        tempo = msg.tempo
        print(f"Tempo: {tempo} ms per beat")
    
    if msg.type == 'note_on':
        frame = int(current_time_seconds * framerate)        
        tl.AddMarker(frame, COLOR, "Beats", "Beat", 1)
        print(f"Marker: {current_time_seconds:.3f}s / {frame}f")

print("Finished adding markers.")
