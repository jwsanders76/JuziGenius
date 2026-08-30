"""
Pre-generates offline sentence audio for the local corpus with piper (MIT),
so sentence playback stops depending on the browser's Web Speech API -- see
project_state.md, section 7 item 15, for the full investigation and the
resource numbers that shaped this script.

Why build-time, not runtime. The droplet this deploys to is 1 vCPU, 458 MB
RAM, ~222 MB available, no swap. Synthesizing on request would mean running
piper's models live there; measured peak RSS for the two voices this script
uses is 191 MB (huayan) and 423 MB (chaowen) -- either eats essentially all
of a request's headroom, and chaowen alone exceeds what the droplet has.
Generating once here and rsyncing static files avoids all of that, the same
pattern fetch_stroke_data.py and build_pinyin_readings.py already use for
their own vendored data.

Scope, by explicit user decision: this covers SENTENCE_SOURCE_FILES (the
17k+-sentence HSK/Tatoeba corpus) only -- not a user's own pasted_sentences,
which don't exist at build time, and not the Tier-1 character-only phase's
single-character items. Playback for anything outside this generated set
falls back to the existing browser Web Speech API (see app.js); a 404 from
GET /api/speech is exactly that signal, by design, not an error to fix here.

Output layout: speech_audio/<voice>/<hash[:2]>/<hash>.mp3, where <hash> is
the sha256 hex digest of the sentence text (spaces stripped, matching how
juzi_engine.py stores and serves `chinese`). Content-addressed rather than
line-numbered so server.py can look up a sentence's audio with no index file
to keep in sync -- it just hashes the text it already has and checks the
path. Sharded into 256 subdirectories per voice so no single directory holds
tens of thousands of files.

This directory is NOT tracked in git (much like stroke_data.json's 29 MB,
but larger: ~292 MB per voice) and is NOT reproducible from a clone in the
way tracked vendored data is -- it must be generated with this script and
rsynced to the droplet. See the README note this should get before shipping.

Requires the piper models on disk locally (not vendored in the repo, since
they're a build-time tool, not shipped data -- see PIPER_MODELS below) and
ffmpeg (falls back to the imageio-ffmpeg pip package's bundled static binary
if no system ffmpeg is on PATH, so `pip install imageio-ffmpeg` is enough on
a machine with no ffmpeg installed at all).

Parallel by default: one PiperVoice loaded per worker process (--workers,
default all CPU cores), voices processed one at a time so peak memory is
workers * one voice's per-process RSS rather than both voices' at once. This
is what turns the measured ~12 core-hours/voice into wall-clock hours rather
than a single-threaded multi-day run.

Usage:
    python3 build_speech_audio.py                  # generate everything missing
    python3 build_speech_audio.py --limit 20        # smoke-test on 20 sentences
    python3 build_speech_audio.py --voices chaowen  # just one voice
    python3 build_speech_audio.py --workers 4       # fewer workers (leaves headroom)
    python3 build_speech_audio.py --dry-run         # count what's missing, do nothing
"""
import argparse
import csv
import hashlib
import io
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave

from juzi_engine import SENTENCE_SOURCE_FILES

VOICES = {
    "chaowen": "models/chaowen/model.onnx",  # male, median F0 163 Hz
    "huayan": "models/huayan/model.onnx",    # female, median F0 214 Hz
}

OUTPUT_DIR = "speech_audio"
MP3_BITRATE = "56k"  # matches the measured ~17 KB/sentence estimate in project_state.md


def collect_sentences():
    """
    Every distinct sentence text SENTENCE_SOURCE_FILES can serve, with spaces
    stripped exactly like juzi_engine.py does before handing `chinese` to the
    frontend -- so the hash this script writes under is the same hash
    server.py will look up at request time.
    """
    seen = set()
    ordered = []
    for path in SENTENCE_SOURCE_FILES:
        if not os.path.exists(path):
            print(f"Warning: {path} not found, skipping.", file=sys.stderr)
            continue
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                chinese = (row.get("sentence") or "").replace(" ", "").strip()
                if chinese and chinese not in seen:
                    seen.add(chinese)
                    ordered.append(chinese)
    return ordered


def sentence_hash(chinese):
    return hashlib.sha256(chinese.encode("utf-8")).hexdigest()


def output_path(voice, chinese):
    h = sentence_hash(chinese)
    return os.path.join(OUTPUT_DIR, voice, h[:2], f"{h}.mp3")


def find_ffmpeg():
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        print(
            "No ffmpeg on PATH and imageio-ffmpeg is not installed. "
            "Run: pip install imageio-ffmpeg", file=sys.stderr,
        )
        sys.exit(1)


def wav_to_mp3(wav_bytes, mp3_path, ffmpeg_bin):
    os.makedirs(os.path.dirname(mp3_path), exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        tmp_wav.write(wav_bytes)
        tmp_wav_path = tmp_wav.name
    try:
        # Written to a temp path first, then renamed into place, so a process
        # killed mid-encode (this is a multi-hour job) never leaves a
        # half-written file at the real path for the server to serve.
        tmp_mp3_path = mp3_path + ".tmp"
        result = subprocess.run(
            [ffmpeg_bin, "-y", "-loglevel", "error", "-i", tmp_wav_path,
             "-ac", "1", "-b:a", MP3_BITRATE, "-codec:a", "libmp3lame",
             "-f", "mp3", tmp_mp3_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr}")
        os.replace(tmp_mp3_path, mp3_path)
    finally:
        os.unlink(tmp_wav_path)


# Set once per worker process by _init_worker, not passed as an argument --
# a PiperVoice isn't picklable, and re-loading the model for every sentence
# (rather than once per worker) would erase most of the point of
# parallelizing at all. One process-global per worker, same shape as
# server.py's module-level caches.
_worker_voice = None
_worker_ffmpeg_bin = None


def _init_worker(model_path, ffmpeg_bin):
    global _worker_voice, _worker_ffmpeg_bin
    import onnxruntime
    from piper import PiperVoice

    # PiperVoice.load() always builds its own SessionOptions with no thread
    # cap, so onnxruntime uses every core for intra-op parallelism inside
    # EACH worker process. With `workers` processes doing that at once they
    # oversubscribe the machine badly -- measured: 8 workers each doing
    # ~0.8 sentences/s (worse than running one at a time), versus ~12/s for
    # a single unconstrained process. Capping each worker's session to one
    # thread is what actually lets `workers` processes run near their solo
    # speed in parallel, which is the entire point of parallelizing this.
    # onnxruntime.InferenceSession is looked up as a module attribute at
    # call time inside piper's own code, so patching it here (module-level,
    # not the imported name) reaches piper's call unmodified otherwise.
    real_session = onnxruntime.InferenceSession

    def _single_threaded_session(*args, **kwargs):
        sess_options = kwargs.get("sess_options") or onnxruntime.SessionOptions()
        sess_options.intra_op_num_threads = 1
        sess_options.inter_op_num_threads = 1
        kwargs["sess_options"] = sess_options
        return real_session(*args, **kwargs)

    onnxruntime.InferenceSession = _single_threaded_session
    try:
        _worker_voice = PiperVoice.load(model_path)
    finally:
        onnxruntime.InferenceSession = real_session
    _worker_ffmpeg_bin = ffmpeg_bin


def _synthesize_one(args):
    voice_name, chinese = args
    mp3_path = output_path(voice_name, chinese)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        _worker_voice.synthesize_wav(chinese, wav_file)
    wav_to_mp3(buf.getvalue(), mp3_path, _worker_ffmpeg_bin)
    return mp3_path


def synthesize_all(voice_name, model_path, sentences, ffmpeg_bin, workers=1, dry_run=False):
    todo = [s for s in sentences if not os.path.exists(output_path(voice_name, s))]
    print(f"[{voice_name}] {len(sentences)} sentences total, "
          f"{len(todo)} missing.", flush=True)
    if dry_run or not todo:
        return

    start = time.time()
    tasks = [(voice_name, s) for s in todo]
    # One PiperVoice per worker process (loaded once in _init_worker, not per
    # sentence) is what actually gets this job down from single-threaded
    # core-hours to wall-clock hours -- see project_state.md section 7 item
    # 15's measured rates, which assumed all 8 dev-box cores in parallel.
    with multiprocessing.Pool(
        processes=workers, initializer=_init_worker, initargs=(model_path, ffmpeg_bin)
    ) as pool:
        for i, _ in enumerate(pool.imap_unordered(_synthesize_one, tasks, chunksize=4), 1):
            if i % 50 == 0 or i == len(todo):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                remaining = (len(todo) - i) / rate if rate > 0 else float("inf")
                print(f"[{voice_name}] {i}/{len(todo)} "
                      f"({rate:.2f}/s, ~{remaining/60:.1f} min left)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N sentences (smoke-testing).")
    parser.add_argument("--voices", default=",".join(VOICES.keys()),
                         help="Comma-separated subset of voices to generate.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Report what's missing without generating anything.")
    parser.add_argument("--workers", type=int, default=multiprocessing.cpu_count(),
                         help="Worker processes per voice (default: all CPU cores). "
                              "Voices are still processed one at a time, so peak "
                              "memory is workers * one voice's per-process RSS, not "
                              "both voices' at once.")
    args = parser.parse_args()

    voice_names = [v.strip() for v in args.voices.split(",") if v.strip()]
    for v in voice_names:
        if v not in VOICES:
            print(f"Unknown voice '{v}'. Known: {', '.join(VOICES)}", file=sys.stderr)
            sys.exit(1)
        if not os.path.exists(VOICES[v]):
            print(f"Missing model file: {VOICES[v]} -- place the piper "
                  f"zh_CN-{v}-medium.onnx (+ .onnx.json) there first.",
                  file=sys.stderr)
            sys.exit(1)

    sentences = collect_sentences()
    if args.limit:
        sentences = sentences[:args.limit]
    print(f"{len(sentences)} distinct sentences from {SENTENCE_SOURCE_FILES}.", flush=True)

    ffmpeg_bin = find_ffmpeg()
    for voice_name in voice_names:
        synthesize_all(voice_name, VOICES[voice_name], sentences, ffmpeg_bin,
                        workers=args.workers, dry_run=args.dry_run)

    print("Done.", flush=True)


if __name__ == "__main__":
    main()
