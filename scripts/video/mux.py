"""Concatenate the takes and lay the narration clips at their recorded marks."""
import json, subprocess, sys
V = ""  # folder that holds the clip-*.wav files written by voice.py
takes = ["main", "live", "end"]
def dur(p): return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p], capture_output=True, text=True).stdout)
offset, clips = 0.0, []
for t in takes:
    mk = json.load(open(f"marks-{t}.json"))
    wall = dict(mk)["__end"] / 1000
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f"take-{t}.webm", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", f"raw-{t}.mp4"], check=True)
    factor = wall / dur(f"raw-{t}.mp4")        # the screencast file runs slower than the clock; put it back on the clock
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f"raw-{t}.mp4", "-vf", f"setpts=PTS*{factor:.6f},fps=30", "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", f"take-{t}.mp4"], check=True)
    print(t, "wall", round(wall, 1), "file", round(dur(f"raw-{t}.mp4"), 1), "factor", round(factor, 4), "->", round(dur(f"take-{t}.mp4"), 1))
    for cid, ms in mk:
        if cid != "__end":
            clips.append((cid, offset + ms / 1000))
    offset += dur(f"take-{t}.mp4")
inputs, filt, labels = [], [], []
for i, (cid, at) in enumerate(clips):
    inputs += ["-i", f"{V}clip-{cid}.wav"]
    filt.append(f"[{i + 3}:a]adelay={int(at * 1000)}:all=1[a{i}]"); labels.append(f"[a{i}]")
fc = "[0:v][1:v][2:v]concat=n=3:v=1:a=0,fps=30,format=yuv420p[v];" + ";".join(filt) + ";" + "".join(labels) + f"amix=inputs={len(clips)}:normalize=0:dropout_transition=0,apad[a]"
cmd = ["ffmpeg", "-v", "error", "-y", "-i", "take-main.mp4", "-i", "take-live.mp4", "-i", "take-end.mp4"] + inputs + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-shortest",
       "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-movflags", "+faststart", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", sys.argv[1] if len(sys.argv) > 1 else "demo-voiced.mp4"]
subprocess.run(cmd, check=True)
print("clips", len(clips), "total", round(offset, 1), "s;", [(c, round(a, 1)) for c, a in clips])
