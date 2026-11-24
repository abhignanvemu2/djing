"""Measure uploaded audio with bounded-memory spectral analysis."""
import json
import math
import subprocess

import numpy as np
from scipy.signal import correlate, find_peaks

RATE = 11025
HOP = 256
WINDOW = 1024
NOTES = "C C# D D# E F F# G G# A A# B".split()


def inspect_file(path, filename):
    result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, timeout=30, check=True)
    info = json.loads(result.stdout)
    streams = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if not streams or info["format"]["format_name"] not in {"mp3", "wav", "aiff", "flac"}:
        raise ValueError("Choose an MP3, WAV, AIFF or FLAC audio file.")
    duration = float(info["format"]["duration"])
    if not math.isfinite(duration) or not 0.5 <= duration <= 3600:
        raise ValueError("Tracks must be between half a second and one hour long.")
    tags = {k.lower(): v for k, v in {**streams[0].get("tags", {}), **info["format"].get("tags", {})}.items()}
    return dict(title=tags.get("title") or filename.rsplit(".", 1)[0], artist=tags.get("artist"),
                genre=tags.get("genre"), duration=round(duration, 2), tagTitle=bool(tags.get("title")),
                key=tags.get("initialkey") or tags.get("tkey"), genreSource="tag" if tags.get("genre") else None)


def analyze(path, meta):
    freqs = np.fft.rfftfreq(WINDOW, 1 / RATE)
    tonal = (freqs >= 65) & (freqs <= 2300)
    pitch = np.rint(69 + 12 * np.log2(freqs[tonal] / 440)).astype(int) % 12
    chroma = np.zeros(12)
    window = np.hanning(WINDOW)
    previous = np.zeros(len(freqs))
    flux, levels, bass, brightness = [], [], [], []
    tail = np.empty(0)
    command = ["ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
               "-i", str(path), "-vn", "-t", "3600", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        for raw in iter(lambda: proc.stdout.read(262144), b""):
            samples = np.concatenate([tail, np.frombuffer(raw, "<i2") / 32768])
            if len(samples) < WINDOW:
                tail = samples
                continue
            frames = np.lib.stride_tricks.sliding_window_view(samples, WINDOW)[::HOP]
            tail = samples[len(frames) * HOP:]
            spectrum = abs(np.fft.rfft(frames * window))
            power = spectrum ** 2
            total = power.sum(axis=1) + 1e-10
            logged = np.log1p(spectrum)
            flux.extend(np.maximum(0, np.diff(logged, axis=0, prepend=previous[None])).sum(axis=1))
            previous = logged[-1]
            levels.extend(np.sqrt(np.mean(frames ** 2, axis=1)))
            bass.extend(power[:, freqs < 180].sum(axis=1) / total)
            brightness.extend(power[:, freqs > 2000].sum(axis=1) / total)
            salience = np.maximum(0, spectrum[:, tonal] - (spectrum[:, tonal].mean(axis=1) * 2)[:, None])
            chroma += np.bincount(pitch, salience.sum(axis=0), minlength=12)
        if proc.wait() or not levels:
            raise ValueError("Audio decoding failed.")
    env = np.array(flux)
    centered = env - env.mean()
    ac = correlate(centered, centered, method="fft")[len(env)-1:]
    low, high = round(60 * RATE / HOP / 185), round(60 * RATE / HOP / 65)
    bpm = groove = None
    if len(ac) > high and ac[0] > 0:
        lag = low + int(np.argmax(ac[low:high + 1]))
        groove = float(np.clip(ac[lag] / ac[0], 0, 1))
        if groove > .13 and meta["duration"] >= 8:
            delta = ac[lag-1] - 2 * ac[lag] + ac[lag+1]
            refined = lag + (0.5 * (ac[lag-1] - ac[lag+1]) / delta if abs(delta) > 1e-9 else 0)
            bpm = round(60 * RATE / HOP / refined, 1)
    scores = []
    if chroma.std() > 1e-7:
        for mode, profile in [("major", [6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88]),
                              ("minor", [6.33,2.68,3.52,5.38,2.6,3.53,2.54,4.75,3.98,2.69,3.34,3.17])]:
            for i, note in enumerate(NOTES):
                scores.append((np.corrcoef(chroma, np.roll(profile, i))[0, 1], f"{note} {mode}"))
    scores.sort(reverse=True)
    key = scores[0][1] if scores and scores[0][0] > .55 and scores[0][0]-scores[1][0] > .06 else None
    rms = np.array(levels)
    active = np.flatnonzero(rms > max(.002, np.quantile(rms, .9) * .06))
    start = round(active[0] * HOP / RATE, 2) if len(active) else 0
    end = min(meta["duration"], round((active[-1] * HOP + WINDOW) / RATE, 2)) if len(active) else 0
    peaks, _ = find_peaks(env, prominence=max(.001, env.std() * .7), distance=4)
    attacks = len(peaks) / meta["duration"]
    energy = round(100 * np.clip(.45 * min(1, np.quantile(rms,.8)/.24) + .35 * min(1, attacks/6) + .2 * min(1, np.mean(brightness)/.16), 0, 1))
    step = round(4 * RATE / HOP)
    sections = np.array([np.mean(rms[i:i+step]) for i in range(0,len(rms),step)])
    changes = np.diff(sections)
    markers = [{"time":i*4,"type":"intensity rise" if x>0 else "intensity fall"} for i,x in enumerate(changes,1)
               if abs(x)>max(.025, sections.mean()*.4)]
    # First and last large intensity changes are cue candidates, pending DJ review.
    rises = [m["time"] for m in markers if m["type"] == "intensity rise" and start < m["time"] < end]
    falls = [m["time"] for m in markers if m["type"] == "intensity fall" and start < m["time"] < end]
    intro = next((v for v in rises if v <= start + (end-start)*.35), None)
    outro = next((v for v in reversed(falls) if v >= start + (end-start)*.65), None)
    return dict(introEnd=intro, outroStart=outro, bpm=bpm, key=meta.get("key") or key, keySource="tag" if meta.get("key") else "estimate",
                energy=energy, pulse=round(groove,2) if groove is not None else None,
                bass=round(float(np.mean(bass)),3), attacks=round(attacks,2), start=float(start), end=float(end),
                markers=markers[:60], waveform=[round(float(x.max()),3) if len(x) else 0 for x in np.array_split(rms,96)])
