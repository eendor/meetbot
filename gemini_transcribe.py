import array, asyncio, contextlib, os, re, signal, subprocess, sys, threading, time
from pathlib import Path
from google import genai
from google.genai import types

HERE = Path(__file__).parent
env_file = HERE / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.5-transcribe-live")
# Languages spoken in the meetings. Pinning them stops the model from
# guessing an unrelated language (e.g. writing Filipino words in Hindi script).
LANGUAGES = [c.strip() for c in os.environ.get("MEETBOT_LANGUAGES", "en-US,fil-PH").split(",") if c.strip()]
RATE = 16000
CHUNK = RATE * 2 // 10  # 100 ms of 16-bit mono PCM
SWITCH_MARGIN = 4  # seconds before a server-requested close to hand off at the latest
DRAIN_TIMEOUT = 4  # seconds to wait for a closing connection's last line
SPLIT_WINDOW = 15  # chunks (1.5 s) searched for the quietest moment to hand off at


def load_vocab():
    """Names and terms from vocab.txt, one per line, bias the model's spelling."""
    path = HERE / "vocab.txt"
    if not path.exists():
        return []
    return [w.strip() for w in path.read_text().splitlines() if w.strip() and not w.startswith("#")]


VOCAB = load_vocab()
out = HERE / "transcripts" / f"{time.strftime('%Y%m%d-%H%M')}.txt"
out.parent.mkdir(exist_ok=True)

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
config = types.LiveConnectConfig(
    response_modalities=["TEXT"],
    input_audio_transcription=types.AudioTranscriptionConfig(
        language_codes=LANGUAGES or None,
        custom_vocabulary=VOCAB or None,
    ),
)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def seconds(time_left, default=10.0):
    """go_away.time_left arrives as a string like '9.5s' (or a timedelta)."""
    if hasattr(time_left, "total_seconds"):
        return time_left.total_seconds()
    m = re.match(r"\s*([\d.]+)", str(time_left or ""))
    return float(m.group(1)) if m else default


def start_capture(loop, queue):
    """Read system audio on a thread so audio keeps buffering while we reconnect."""
    ff = subprocess.Popen(
        ["ffmpeg", "-loglevel", "fatal", "-f", "pulse", "-i", "@DEFAULT_MONITOR@",
         "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )

    def reader():
        while chunk := ff.stdout.read(CHUNK):
            loop.call_soon_threadsafe(queue.put_nowait, chunk)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=reader, daemon=True).start()
    return ff


def quietest_point(pcm, frame=320):
    """Byte offset of the quietest 20 ms in the audio, usually a gap between words."""
    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    best, best_energy = 0, None
    for i in range(0, len(samples) - frame + 1, frame):
        energy = sum(x * x for x in samples[i:i + frame])
        if best_energy is None or energy < best_energy:
            best, best_energy = i + frame // 2, energy
    return best * 2


class Transcript:
    def __init__(self, f):
        self.f = f

    def write(self, text, partial=False, at=None):
        text = text.strip()
        if not text:
            return
        stamp = time.strftime('%H:%M:%S', time.localtime(at))
        entry = f"[{stamp}] {text}{' …' if partial else ''}"
        self.f.write(entry + "\n"); self.f.flush(); print(entry, flush=True)


class Link:
    """One live connection plus the transcript text it has produced."""

    def __init__(self, tr):
        self.tr = tr
        self.stack = contextlib.AsyncExitStack()
        self.session = None
        self.pending = ""            # latest interim text not yet finalized
        self.final = asyncio.Event()  # set whenever a finalized line arrives
        self.go_away = asyncio.Event()
        self.deadline = None
        self.held = None              # list while lines must wait for the previous link
        self.rx = None

    async def open(self):
        try:
            self.session = await self.stack.enter_async_context(
                client.aio.live.connect(model=MODEL, config=config))
        except BaseException:
            await self.stack.aclose()
            raise
        self.rx = asyncio.create_task(self.receive())
        return self

    def emit(self, text):
        if self.held is not None:
            self.held.append((time.time(), text))
        else:
            self.tr.write(text)

    async def receive(self):
        while True:
            async for msg in self.session.receive():
                if msg.go_away and not self.go_away.is_set():
                    secs = seconds(msg.go_away.time_left)
                    self.deadline = time.monotonic() + secs
                    log(f"server will close connection in {secs:.0f}s; preparing handoff")
                    self.go_away.set()
                sc = msg.server_content
                if not sc:
                    continue
                if sc.interim_input_transcription and sc.interim_input_transcription.text:
                    self.pending = sc.interim_input_transcription.text
                if sc.input_transcription and sc.input_transcription.text:
                    self.pending = ""
                    self.emit(sc.input_transcription.text)
                    self.final.set()

    def release(self):
        """Write lines that were held back while the previous link finished."""
        for at, text in self.held or []:
            self.tr.write(text, at=at)
        self.held = None

    async def finish(self, timeout=DRAIN_TIMEOUT):
        """Tell the server no more audio is coming and collect the last line."""
        if self.rx.done():
            return
        self.final.clear()
        try:
            await self.session.send_realtime_input(audio_stream_end=True)
        except Exception as e:
            log(f"could not end audio stream cleanly: {str(e)[:120]}")
            return
        if self.pending:
            await first(self.final.wait(), self.rx, timeout=timeout)

    async def close(self):
        if self.rx:
            self.rx.cancel()
            await asyncio.gather(self.rx, return_exceptions=True)
        if self.pending:  # never finalized; keep what we heard rather than lose it
            self.tr.write(self.pending, partial=True)
            self.pending = ""
        self.release()
        with contextlib.suppress(Exception):
            await self.stack.aclose()


async def first(*aws, timeout=None):
    """Wait until any of the awaitables/tasks finishes (or timeout). Never raises."""
    tasks = [a if isinstance(a, asyncio.Future) else asyncio.ensure_future(a) for a in aws]
    try:
        await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t, a in zip(tasks, aws):
            if t is not a:
                t.cancel()
                if t.done() and not t.cancelled():
                    t.exception()  # mark as retrieved; callers inspect state instead


class Stream:
    """Feeds audio to the current link and hands off to a fresh one before the server
    closes the old one, so no speech is dropped at reconnects."""

    def __init__(self, queue, tr):
        self.queue, self.tr = queue, tr
        self.links = []
        self.spare = None
        self.handoff = None           # next link, waiting for pump to switch to it
        self.switched = asyncio.Event()

    @property
    def cur(self):
        return self.links[-1]

    async def send(self, link, pcm):
        await link.session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={RATE}"))

    async def pump(self):
        eof = False
        while not eof:
            chunk = await self.queue.get()
            if chunk is None:
                break
            if self.handoff:
                # Collect a little audio and switch links at its quietest moment,
                # so a word is less likely to be split across two connections.
                buf = [chunk]
                while len(buf) < SPLIT_WINDOW:
                    nxt = await self.queue.get()
                    if nxt is None:
                        eof = True
                        break
                    buf.append(nxt)
                pcm = b"".join(buf)
                cut = quietest_point(pcm)
                if cut:
                    await self.send(self.cur, pcm[:cut])
                self.links.append(self.handoff)
                self.handoff = None
                self.switched.set()
                chunk = pcm[cut:]
            if chunk:
                await self.send(self.cur, chunk)
        await self.cur.finish()  # capture stopped: finalize the last sentence
        raise EOFError("audio capture stopped")

    async def run(self):
        self.links.append(await Link(self.tr).open())
        log("connected")
        pump = asyncio.create_task(self.pump())
        try:
            while True:
                old = self.cur
                await first(pump, old.rx, old.go_away.wait())
                self.check(pump, old)

                new = self.spare = Link(self.tr)
                new.held = []
                await first(new.open(), pump, old.rx,
                            timeout=max(old.deadline - time.monotonic() - 1, 1))
                self.check(pump, old)
                if new.session is None:
                    raise ConnectionError("could not open next connection in time")
                log("next connection ready")

                # Hand off at a natural pause if one comes, otherwise just before the deadline.
                # (During silence there's nothing to split, so switch right away.)
                if old.pending:
                    old.final.clear()
                    wait = max(old.deadline - time.monotonic() - SWITCH_MARGIN, 0)
                    await first(old.final.wait(), pump, old.rx, timeout=wait)
                self.switched.clear()
                self.handoff = new  # pump switches audio to the new link at a quiet moment
                await first(self.switched.wait(), pump, old.rx)
                if self.handoff:  # old link died before pump could switch
                    self.handoff = None
                    self.links.append(new)
                self.spare = None
                self.check(pump, old, allow_closed=True)
                await old.finish(timeout=min(DRAIN_TIMEOUT, max(old.deadline - time.monotonic(), 0.5)))
                self.links.remove(old)
                await old.close()
                new.release()
                log("switched to next connection")
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            for link in self.links + ([self.spare] if self.spare else []):
                await link.close()
            self.links.clear()
            self.spare = None

    @staticmethod
    def check(pump, link, allow_closed=False):
        if pump.done():
            pump.result()  # re-raises EOFError or a send error
        if link.rx.done() and not allow_closed:
            exc = link.rx.exception()
            raise exc or ConnectionError("connection closed")


async def main():
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    ff = start_capture(loop, queue)

    def stop():
        # First Ctrl+C: stop capture and let the last sentence finish. Second: quit now.
        if ff.poll() is None:
            log("stopping…")
            ff.terminate()
        else:
            raise KeyboardInterrupt

    loop.add_signal_handler(signal.SIGINT, stop)
    loop.add_signal_handler(signal.SIGTERM, stop)

    print(f"Listening with {MODEL}... writing to {out} (Ctrl+C to stop)", flush=True)
    if VOCAB:
        log(f"vocabulary: {', '.join(VOCAB)}")
    backoff = 1
    with open(out, "a") as f:
        tr = Transcript(f)
        try:
            while True:
                started = time.monotonic()
                try:
                    await Stream(queue, tr).run()
                except EOFError:
                    break
                except Exception as e:
                    if time.monotonic() - started > 60:
                        backoff = 1
                    log(f"reconnecting in {backoff}s after error: {str(e)[:200]}")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
        finally:
            ff.terminate()
    if out.exists() and out.stat().st_size == 0:
        out.unlink()  # nothing was transcribed; don't leave an empty file behind


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
