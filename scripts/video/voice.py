import asyncio, json, subprocess, sys, edge_tts
HERE = __import__("pathlib").Path(__file__).resolve().parent
VOICE = sys.argv[1] if len(sys.argv) > 1 else "en-US-BrianMultilingualNeural"
RATE = sys.argv[2] if len(sys.argv) > 2 else "+4%"
lines = json.load(open(HERE / "lines.json"))
async def main():
    for k, v in lines.items():
        await edge_tts.Communicate(v["say"], VOICE, rate=RATE).save(f"clip-{k}.mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f"clip-{k}.mp3", "-af", "silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "2", f"clip-{k}.wav"], check=True)
        d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f"clip-{k}.wav"], capture_output=True, text=True).stdout)
        v["dur"] = round(d * 1000)
    json.dump(lines, open("lines.timed.json", "w"), indent=1, ensure_ascii=False)
    print(VOICE, RATE, "总时长", round(sum(v["dur"] for v in lines.values()) / 1000, 1), "s;", {k: v["dur"] for k, v in lines.items()})
asyncio.run(main())
