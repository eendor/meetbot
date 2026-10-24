#!/usr/bin/env bash
cd "$(dirname "$0")" && exec systemd-inhibit --what=sleep:idle --who=meetbot \
  --why="Transcribing meeting" venv/bin/python gemini_transcribe.py "$@"
