"""Which name could replace "Kaki": the one every ear hears and ordinary talk doesn't wake.

Why: on 2026-10-20 MERaLiON wrote the name "Kakki", "Kak Kee" and
"Cappie", and Groq wrote "Khaki". The owner asked for a name that is easy
for her to call and hard to mishear. This measures candidates rather than
guessing. Each name is said in Singlish (Google en-IN/GB/AU/US, normal and
slow, plus the name said alone), Mandarin, Malay and Tamil sentences. It is
scored on three ears: tiny.en with the wake rule from audio/wake.py (the
local wake), and MERaLiON and Groq (the cloud transcribers, which need the
exact spelling). Ordinary sentences are also run through tiny.en with each
name as its prompt, to count false wakes.

Each result is appended to results.jsonl as it arrives, so a stopped run
resumes where it stopped. Groq's free tier allows 20 requests a minute and
MERaLiON's 60, which Saathi also uses: don't run this while Saathi runs.

    set -a; . ./.env; set +a
    uv run python scripts/name_bench.py            # about 20 min the first time
    uv run python scripts/name_bench.py --report   # the table again, no calls
"""

import argparse
import difflib
import json
import os
import pathlib
import re
import time
import wave

import numpy as np

NAMES = [
    "Kaki",
    "Coco",
    "Momo",
    "Mimi",
    "Kiki",
    "Jojo",
    "Tina",
    "Nina",
    "Ruby",
    "Molly",
    "Polly",
    "Teddy",
    "Benny",
    "Lulu",
    "Hana",
    "Mona",
    "Sakura",
    "Melody",
    "Daisy",
    "Bella",
]
EN = [
    "en-IN-Wavenet-A",
    "en-IN-Wavenet-B",
    "en-GB-Wavenet-A",
    "en-GB-Wavenet-B",
    "en-AU-Wavenet-B",
    "en-US-Wavenet-D",
]
DISTRACT = [
    "Can you come here?",
    "Okay lah, never mind.",
    "The cocoa very nice.",
    "Mummy, where are you?",
    "Me me me, always me.",
    "Ruby ring very expensive.",
    "My teddy bear is old.",
    "Tina is my neighbour.",
    "Come come, sit down.",
    "Kick the ball.",
    "Nine o'clock already.",
    "Holy moly.",
    "Hello hello.",
    "Mama, go home.",
    "Kakak, bring the water.",
    "Melon very sweet.",
    "Daily also like that.",
    "Belly very full.",
    "Coffee or tea?",
    "Lunch already or not?",
    "Honey, come here.",
    "Sorry ah.",
    "Polo shirt.",
    "Money no enough.",
]
GROQ_PER_MINUTE = 18  # under the free tier's 20, leaving room for nothing else


def frames(name):
    f = []
    for v in EN:
        f += [
            (v, 1.0, "en", f"{name}, can you call my son?"),
            (v, 1.0, "en", f"{name}, what time is it?"),
        ]
    return f + [
        ("en-IN-Wavenet-A", 0.8, "en", f"{name}, remind me take my medicine."),
        ("en-GB-Wavenet-B", 0.8, "en", f"{name}, remind me take my medicine."),
        ("en-IN-Wavenet-B", 1.0, "en", f"{name}?"),
        ("en-US-Wavenet-D", 1.0, "en", f"{name}?"),
        ("cmn-CN-Wavenet-A", 1.0, "zh", f"{name}，今天天气很好。"),
        ("ms-MY-Wavenet-A", 1.0, "ms", f"{name}, tolong telefon anak saya."),
        ("ta-IN-Wavenet-A", 1.0, "ta", f"{name}, இன்று என்ன நாள்?"),
    ]


def synth(client, voice, rate, text, path):
    """One clip, cached; waits out Google's 50-a-minute limit."""
    from google.api_core.exceptions import ResourceExhausted
    from google.cloud import texttospeech as tts

    if path.exists():
        return
    lang = "-".join(voice.split("-")[:2])
    for _ in range(30):
        try:
            r = client.synthesize_speech(
                input=tts.SynthesisInput(text=text),
                voice=tts.VoiceSelectionParams(language_code=lang, name=voice),
                audio_config=tts.AudioConfig(
                    audio_encoding=tts.AudioEncoding.LINEAR16,
                    sample_rate_hertz=16000,
                    speaking_rate=rate,
                ),
            )
            path.write_bytes(r.audio_content)
            return
        except ResourceExhausted:
            time.sleep(15)
    raise RuntimeError(f"Google TTS kept refusing {path.name}")


def wakes(text, name):
    """audio/wake.py's rule with `name` in place of "kaki": within the
    first three words, 0.88 similar, one word or two joined."""
    target = name.lower()
    words = re.findall(r"[a-z]+", text.lower())

    def near(w):
        return difflib.SequenceMatcher(None, w, target).ratio() >= 0.88

    return any(
        near(words[i]) or (i + 1 < len(words) and near(words[i] + words[i + 1]))
        for i in range(min(len(words), 3))
    )


def spelled(text, name):
    return name.lower() in re.findall(r"[a-z]+", text.lower())[:3]


def report(out):
    rows = [json.loads(line) for line in (out / "results.jsonl").open()]
    clips = {(r["name"], r["k"]): r for r in rows if r["kind"] == "clip"}
    false = {}
    for r in rows:
        if r["kind"] == "distract" and r["woke"]:
            false.setdefault(r["name"], []).append(f"{r['said']!r} -> {r['tiny']!r}")
    n = len(frames("x"))
    ranked = []
    for name in NAMES:
        rs = [clips[(name, k)] for k in range(n) if (name, k) in clips]
        if not rs:
            continue
        t = sum(wakes(r["tiny"], name) for r in rs)
        m = sum(spelled(r["meralion"], name) for r in rs)
        g = sum(spelled(r["groq"], name) for r in rs)
        all3 = sum(
            wakes(r["tiny"], name) and spelled(r["meralion"], name) and spelled(r["groq"], name)
            for r in rs
        )
        ranked.append((all3 - 2 * len(false.get(name, [])), name, len(rs), t, m, g, all3))
    print(
        f"\n{'name':8s} clips  tiny.en  MERaLiON  Groq  all-3  false wakes (of {2 * len(DISTRACT)})"
    )
    for _, name, c, t, m, g, a in sorted(ranked, reverse=True):
        print(f"{name:8s} {c:4d}   {t:5d}   {m:6d}   {g:4d}  {a:5d}  {len(false.get(name, []))}")
    for name, hits in false.items():
        print(f"\n{name} woke on:", *hits, sep="\n  ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", type=pathlib.Path, default=pathlib.Path.home() / ".saathi" / "name-bench"
    )
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    if args.report:
        return report(out)

    from faster_whisper import WhisperModel
    from google.cloud import texttospeech as tts
    from saathi.voice.engine.meralion import MERALION_STT_MODEL, MeralionClient
    from saathi.voice.engine.provider import GROQ_STT_MODEL, build_client

    results = out / "results.jsonl"
    done = set()
    if results.exists():
        for line in results.open():
            r = json.loads(line)
            done.add((r["kind"], r["name"], r["k"]))

    google = tts.TextToSpeechClient()
    tiny = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    meralion = MeralionClient(os.environ["MERALION_API_KEY"], timeout=20)
    groq = build_client("groq", os.environ["GROQ_API_KEY"])

    def hear_locally(path, prompt):
        with wave.open(str(path)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        segs, _ = tiny.transcribe(
            pcm,
            beam_size=1,
            language="en",
            initial_prompt=prompt,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        return " ".join(s.text for s in segs).strip()

    def cloud(ear, call):
        for attempt in range(5):
            try:
                return call().text
            except Exception as exc:  # 429s mostly; anything else is recorded
                if "429" not in str(exc) and "rate" not in str(exc).lower():
                    return f"ERROR {exc}"
                time.sleep(10 * (attempt + 1))
        return f"ERROR {ear} rate-limited five times"

    gap = 60 / GROQ_PER_MINUTE
    with results.open("a") as log:
        for k, said in enumerate(DISTRACT):
            for v in ("en-IN-Wavenet-A", "en-GB-Wavenet-B"):
                path = out / f"d_{k:02d}_{v}.wav"
                synth(google, v, 1.0, said, path)
                for name in NAMES:
                    key = ("distract", name, f"{k}-{v}")
                    if key in done:
                        continue
                    heard = hear_locally(path, name)
                    log.write(
                        json.dumps(
                            {
                                "kind": "distract",
                                "name": name,
                                "k": f"{k}-{v}",
                                "said": said,
                                "tiny": heard,
                                "woke": wakes(heard, name),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        log.flush()
        todo = [
            (name, k, f)
            for name in NAMES
            for k, f in enumerate(frames(name))
            if ("clip", name, k) not in done
        ]
        print(f"{len(todo)} clips to hear, about {len(todo) * gap / 60:.0f} min", flush=True)
        for i, (name, k, (voice, rate, lang, said)) in enumerate(todo, 1):
            started = time.monotonic()
            path = out / f"{name}_{k:02d}.wav"
            synth(google, voice, rate, said, path)
            audio = path.read_bytes()
            row = {
                "kind": "clip",
                "name": name,
                "k": k,
                "voice": voice,
                "said": said,
                "tiny": hear_locally(path, name),
                "meralion": cloud(
                    "meralion",
                    lambda: meralion.audio.transcriptions.create(
                        model=MERALION_STT_MODEL, file=(path.name, audio)
                    ),
                ),
                "groq": cloud(
                    "groq",
                    lambda: groq.audio.transcriptions.create(
                        model=GROQ_STT_MODEL,
                        file=(path.name, audio),
                        response_format="json",
                        language=lang,
                    ),
                ),
            }
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            if i % 19 == 0:
                print(
                    f"{i}/{len(todo)}  last: {name}: {row['meralion']!r} / {row['groq']!r}",
                    flush=True,
                )
            time.sleep(max(0.0, gap - (time.monotonic() - started)))
    report(out)


if __name__ == "__main__":
    main()
