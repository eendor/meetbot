import os, queue, signal, subprocess, threading, time
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext

HERE = Path(__file__).parent
TRANSCRIPTS = HERE / "transcripts"


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.started = None
        self.lines = queue.Queue()

        root.title("Meet Transcriber")
        root.geometry("720x480")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        bar = tk.Frame(root, padx=8, pady=8)
        bar.pack(fill="x")
        self.btn = tk.Button(bar, text="● Start", width=10, bg="#2e7d32", fg="white",
                             font=("Sans", 12, "bold"), command=self.toggle)
        self.btn.pack(side="left")
        tk.Button(bar, text="Open folder", command=self.open_folder).pack(side="left", padx=6)
        self.clock = tk.Label(bar, text="00:00:00", font=("Monospace", 12))
        self.clock.pack(side="right")

        self.text = scrolledtext.ScrolledText(root, wrap="word", font=("Sans", 11),
                                              state="disabled", padx=8, pady=8)
        self.text.pack(fill="both", expand=True, padx=8)

        self.status = tk.Label(root, text="Idle", anchor="w", fg="#555", padx=8, pady=4)
        self.status.pack(fill="x")

        self.poll()

    def toggle(self):
        self.stop() if self.proc else self.start()

    def start(self):
        self.text.configure(state="normal"); self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.proc = subprocess.Popen(
            [str(HERE / "transcribe.sh")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        for stream, kind in ((self.proc.stdout, "out"), (self.proc.stderr, "err")):
            threading.Thread(target=self.read, args=(stream, kind), daemon=True).start()
        self.started = time.monotonic()
        self.btn.configure(text="■ Stop", bg="#c62828")
        self.status.configure(text="Starting…")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.status.configure(text="Stopping…")
            os.killpg(self.proc.pid, signal.SIGINT)

    def read(self, stream, kind):
        for line in stream:
            self.lines.put((kind, line.rstrip()))

    def poll(self):
        while not self.lines.empty():
            kind, line = self.lines.get()
            if kind == "out" and line.startswith("Listening"):
                self.status.configure(text=line)
            elif kind == "out":
                self.text.configure(state="normal")
                self.text.insert("end", line + "\n"); self.text.see("end")
                self.text.configure(state="disabled")
                self.status.configure(text=f"Recording — last line at {time.strftime('%H:%M:%S')}")
            elif line.strip():
                self.status.configure(text=line[:150])

        if self.proc:
            if self.proc.poll() is None:
                s = int(time.monotonic() - self.started)
                self.clock.configure(text=f"{s // 3600:02}:{s % 3600 // 60:02}:{s % 60:02}")
            else:
                self.proc = None
                self.btn.configure(text="● Start", bg="#2e7d32")
                self.status.configure(text="Stopped — transcript saved in transcripts/")
        self.root.after(200, self.poll)

    def open_folder(self):
        TRANSCRIPTS.mkdir(exist_ok=True)
        subprocess.Popen(["xdg-open", str(TRANSCRIPTS)])

    def on_close(self):
        if self.proc and self.proc.poll() is None:
            self.stop()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
