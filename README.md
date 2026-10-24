# Meetbot 🎙️

A real-time Linux meeting audio transcriber powered by Google Gemini Live API (`gemini-3.5-transcribe-live`) with seamless connection handoffs, custom vocabulary bias, and a lightweight desktop GUI.

---

## Features

- **Live System Audio Capture**: Captures desktop / meeting output directly from PulseAudio or PipeWire via `@DEFAULT_MONITOR@`.
- **Gemini Live Streaming**: Streams 16 kHz 16-bit PCM audio directly to Google Gemini's live multimodal/audio transcription endpoints.
- **Seamless Session Handoff**: Automatically negotiates and swaps websocket sessions ahead of Gemini Live session limits without dropping audio.
- **Custom Vocabulary**: Add specialized terminology, participant names, or acronyms to `vocab.txt` to guide model transcription accuracy.
- **Multilingual Support**: Configurable language hinting (defaults to `en-US,fil-PH`).
- **Tkinter GUI**: Simple interface with start/stop control, elapsed session timer, live transcript stream, and quick folder access.
- **Sleep Inhibition**: Integrates with `systemd-inhibit` to prevent system sleep / idle during active meeting transcription.

---

## Prerequisites

- **Linux** with PulseAudio or PipeWire (`pipewire-pulse`)
- **FFmpeg**: Required for audio monitoring (`ffmpeg`)
- **Python 3.10+** with `tkinter`
- **Google Gemini API Key**

On Arch Linux:
```bash
sudo pacman -S ffmpeg tk python
```

On Ubuntu / Debian:
```bash
sudo apt update
sudo apt install ffmpeg python3-tk python3-venv
```

---

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/eendor/meetbot.git
   cd meetbot
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Configure your API key:
   ```bash
   cp .env.example .env
   # Edit .env and insert your GEMINI_API_KEY
   ```

---

## Usage

### Using the GUI

Launch the desktop interface:
```bash
python3 gui.py
```
Click **● Start** to begin transcribing. Output will be displayed live in the window and saved to the `transcripts/` directory as timestamped text files (`YYYYMMDD-HHMM.txt`).

### Using CLI / Shell Directly

You can also run the transcription engine directly via:
```bash
./transcribe.sh
```

### Custom Vocabulary

Bias the model towards specialized jargon or domain terms by creating a `vocab.txt` (see `vocab.txt.example`):
```bash
cp vocab.txt.example vocab.txt
```
Add one word or phrase per line. Lines starting with `#` are ignored.

---

## Desktop Entry

To launch Meetbot from your application launcher or desktop, copy the `.desktop` file:
```bash
cp meetbot.desktop ~/.local/share/applications/
```
*(Make sure the paths inside `meetbot.desktop` point to your local installation.)*

---

## License

MIT License. See [LICENSE](LICENSE) for details.
