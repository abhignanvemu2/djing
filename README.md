# MixFlow AI

A DJ set preparation studio. Import your recordings, choose compatible genres, plan a four-stage energy journey, and take a timed running order to the booth.

## Start on Windows with PowerShell

The default studio runs on your PC with no subscription, API key or per-song fee. Install Python 3.12 and FFmpeg (including ffprobe), then open PowerShell in the project folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open **http://localhost:8000** and create your studio account. You can import recordings and build a timed set immediately. To enable the conversational DJ assistant, follow the local model setup below.

## Run with Docker or another Python environment

Install Docker Desktop or Docker Engine, then run from this repository:

```sh
docker build -t mixflow .
docker run --name mixflow -p 127.0.0.1:8000:8000 -v mixflow-data:/app/data mixflow
```

Open **http://localhost:8000**, select **Create an account**, and create a project. Restart the same container with `docker start mixflow`. The named volume holds accounts, private recordings, project state and versions.

Alternatively, install Python 3.12 and FFmpeg (both ffmpeg and ffprobe on PATH), then run:

```sh
python -m pip install -r requirements.txt
python app.py
```

## Audio analysis and curation

The browser uploads recordings to an authenticated private library. FFprobe reads metadata. Background jobs decode audio and measure its amplitude envelope, transients, spectral balance, pitch-class distribution and onset periodicity. Tempo and key estimates can be wrong, particularly at half/double tempo or for changing keys. The inspector shows evidence, identifies uncertainty and allows per-project DJ corrections. The waveform marks intensity changes. Listen before treating these as confirmed breakdowns or drops.

The included curator evaluates the analyzed library against stage targets, duration, tempo movement, harmonic relationships, bass continuity, artist repetition, genre preferences and observed rhythm. It budgets audible duration and overlap and displays estimated play and start times. A short library can produce a shorter set. Levels and overlap estimates are planning aids. They do not describe audience response or guarantee beatmatched transitions.

## Enable model-based curation and chat

For free local inference on Windows, install Ollama and quit it from the system tray if it is running. Open PowerShell and start Ollama in local-only mode:

```powershell
$env:OLLAMA_NO_CLOUD="1"
ollama serve
```

Leave that window open. In a second PowerShell window, open the project folder. Download the local model once and start MixFlow:

```powershell
ollama pull qwen2.5:7b
$env:MIXFLOW_MODEL="qwen2.5:7b"
$env:OLLAMA_URL="http://127.0.0.1:11434"
.\.venv\Scripts\python.exe app.py
```

If you already have a compatible local model in Ollama, use its name from `ollama list` for `MIXFLOW_MODEL` and skip the download. Local inference uses your computer’s memory and electricity. [Ollama local-only settings](https://docs.ollama.com/faq)

For Docker Desktop add `-e MIXFLOW_MODEL=qwen2.5:7b -e OLLAMA_URL=http://host.docker.internal:11434` before the image name and ensure the container can reach Ollama. Each request includes the entire project's structured audio evidence, existing running order and preferences. Model output is validated against project IDs and stage names. Proposed changes to an existing set require confirmation. If the configured model is unavailable, the studio reports the error and retains the previous tracklist. Without a model, the studio uses the measured-feature sequencer and labels chat as unavailable.

## Optional audio classification

In the native Python environment, install compatible PyTorch, torchaudio and Transformers packages (Transformers 4.x), then set `MIXFLOW_CLAP_MODEL` to a local CLAP checkpoint or a compatible model identifier such as `laion/clap-htsat-unfused`. Start the app after configuring it. First-time download requires model host access. Classification runs on three excerpts and compares candidate genres, moods and vocal labels. Low-margin predictions remain open for review. Stored genre tags take priority. Classifier configuration is optional and its weights are not bundled with the Docker image.

## Projects and exports

Project preferences and tracklist edits save automatically. Duplicate projects share private source recordings while preserving independent set state. A project can contain multiple named snapshots. The alternatives tool stores distinct sequences when the library supports them. Compare versions before loading one. Export CSV or tab-separated text, copy the tracklist, or select **Print / PDF** and choose **Save as PDF** in the browser.

## Operating scope

This implementation uses Python, a browser client, SQLite WAL and a bounded background thread pool on one host. Interrupted analysis jobs resume on server restart. Run one application process per data directory. Use `MIXFLOW_WORKERS` to adjust analysis concurrency and `MIXFLOW_DATA` to set storage location. Each file is limited to 250 MB and one hour. Keep the default loopback binding for personal use. For shared hosting, place behind an HTTPS reverse proxy with request limits, rate limiting and storage quotas, set `MIXFLOW_HTTPS=1` for secure cookies, and back up the entire data directory. Multi-host queuing, object storage, managed account recovery and production load testing are outside this implementation.
