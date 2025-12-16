# the purpose of this is to quickly run many instances of noise removal with different parameters on the same file so that they could be tested by listening to the files.
# it's easy to a-b listen multiple files in audacity with the "solo" button while switching

import noisereduce as nr
from scipy.io import wavfile
import os
import warnings
import numpy as np

warnings.filterwarnings("ignore")

#track_types = ["treble", "rhytm", "bass"]
#win_lengths = [4096, 8192] 
#fft_multipliers = [1, 2, 4] 
#hop_length_ratios = [4, 8, 16] 
#std_thresholds = [0.5, 1.0, 1.5] 
#prop_decreases = [0.95, 1.0, 1.05] 
#freq_smooths = [None] 
#time_smooths = [None]

track_types = ["treble", "rhytm", "bass"]
win_lengths = [4096] 
fft_multipliers = [8] # 16 used 40 GB of ram !!
hop_length_ratios = [8] # 32 used 20gb of ram
std_thresholds = [1.57]
prop_decreases = [1.08] 
freq_smooths = [80] 
time_smooths = [60]

def fmt(v, prefix):
    if v is None:
        return f"{prefix}None"
    if isinstance(v, float):
        return f"{prefix}{str(v).replace('.', 'p')}"
    return f"{prefix}{v}"

def get_filename(track, win_len, fft_mult, hop_ratio,
                 std_thresh, prop_dec, freq_smooth, time_smooth):

    return (
        f"cleaned_{track}"
        f"_len{win_len}"
        f"_fft{fft_mult}"
        f"_hop{hop_ratio}"
        f"_std{str(std_thresh).replace('.', 'p')}"
        f"_prop{str(prop_dec).replace('.', 'p')}"
        f"_{fmt(freq_smooth, 'fs')}"
        f"_{fmt(time_smooth, 'ts')}"
        ".wav"
    )

def process_combo(track, signal, noise, rate, win_len, fft_mult, hop_ratio, std_thresh, prop_dec, freq_smooth, time_smooth):
    
    n_fft = win_len * fft_mult
    hop_len = int(win_len / hop_ratio)
    
    output_filename = get_filename(
        track, win_len, fft_mult, hop_ratio,
        std_thresh, prop_dec, freq_smooth, time_smooth
    )
    
    reduced_noise = nr.reduce_noise(
        y=signal,
        sr=rate,
        y_noise=noise,
        stationary=True,
        n_fft=n_fft,
        win_length=win_len,
        hop_length=hop_len,
        n_std_thresh_stationary=std_thresh, 
        prop_decrease=prop_dec,         
        freq_mask_smooth_hz=freq_smooth,    
        time_mask_smooth_ms=time_smooth,    
        chunk_size=18522000, # 7 minutes of audio
        n_jobs=1
    )
    wavfile.write(output_filename, rate, reduced_noise)

for track in track_types:
    input_filename = f"shift-corrected {track}.wav"
    noise_filename = f"noise {track}.wav"

    rate, signal = wavfile.read(input_filename)
    rate_noise, noise = wavfile.read(noise_filename)
    
    signal = signal.astype(np.float32)
    noise = noise.astype(np.float32)

    for win_len in win_lengths:
        for fft_mult in fft_multipliers:
            for hop_ratio in hop_length_ratios:
                for std_thresh in std_thresholds:
                    for prop_dec in prop_decreases:
                        for freq in freq_smooths:
                            for ms in time_smooths:
                                
                                process_combo(
                                    track, signal, noise, rate, 
                                    win_len, fft_mult, hop_ratio, 
                                    std_thresh, prop_dec, freq, ms
                                )
