# News Highlights Video Tool (personal use)

Collects news text and images from outlets you choose, has Claude write a short highlights
script, adds photos of public figures named in the stories, records a human-sounding voiceover,
and builds a WhatsApp-ready MP4 (about 16 MB or less, split into parts if longer).
At the end of every run it asks whether you want to download the video.

No video is scraped. Only text and images are used.

## 1. Setup (one time)

1. Install Python 3.11 or newer.
2. Install FFmpeg
   - Windows (PowerShell): `winget install Gyan.FFmpeg`, then reopen the terminal
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg`
3. In this folder: `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in:
   - `ANTHROPIC_API_KEY` (required for real scripts)
   - optional: `PEXELS_API_KEY`, `PIXABAY_API_KEY` (stock images), ElevenLabs/OpenAI keys
5. Open `config.yaml` and check the outlets. The feed URLs are my best guesses and
   were not verified from here; open each in a browser and fix any that fail.

## 2. First run

```
python main.py run-all --dry-run     # tests everything with mock script + silent voice, no API cost
python main.py voices                # lists available voices
python main.py voice-test            # plays a sample in your configured voice
python main.py run-all               # the real thing
```

During a run, any public figure found in a story is shown to you for approval
(`python main.py approve-people` to do it separately). Unapproved photos are never used.
You can also drop your own photos into `data/people_photos/` (file name = person's name).
After the video is made you are asked whether to download it. Files are copied to your
Downloads folder with a timestamp and never overwrite earlier ones.

Single steps: `collect`, `select`, `images`, `people`, `approve-people`, `script`, `voice`,
`assemble`, `review`, `export`, `download`. Flags: `--config --date --dry-run --force -v`.
`run-all` also accepts `--skip-images --skip-collect --yes --no-download-prompt --no-approve-prompt`.

## 3. Voice

Default is edge-tts (free, Nigerian English voices). Alternatives in `config.yaml`
(`tts.provider` / `tts.fallback`): kokoro, piper, elevenlabs, openai, own_voice (XTTS cloning
of a recording of yourself; only clone your own voice or one you have permission for).
With a failure the next provider in the fallback order is used.

## 4. Scheduling

- Windows: Task Scheduler, action `python main.py run-all --yes --no-download-prompt`, start in this folder.
- Linux/macOS cron: `0 7 * * * cd /path/to/news_highlights && python3 main.py run-all --yes --no-download-prompt`

## 5. Cost control

`general.max_claude_calls_per_run` (default 20) caps API calls. Switch the model in
`config.yaml` to `claude-haiku-5-5` for a cheaper run.

## 6. Copyright and monetisation

This is for personal use. Article text is only summarised, each story credits its source, and
images keep their credit line. Outlet photos belong to the outlets and wire agencies. If you ever
publish the videos publicly or earn from them, set `monetization_safe: true` in `config.yaml`:
it restricts images to stock sources and licence-checked Wikimedia photos (CC0, public domain,
CC BY, CC BY-SA). Public-figure photos are found by name only, never by facial recognition.
The tool respects robots.txt and skips any page that asks for login or payment.

## 7. Tests

`python -m pytest tests/ -q` (23 offline tests, including a full dry-run pipeline).

## 8. What was not tested

Built and tested offline. Not exercised against the live internet: real RSS feeds, Wikipedia
lookups, the Claude API, edge-tts, Kokoro, Piper, XTTS, Pexels/Pixabay and news APIs.
If a step fails, run it alone with `-v` and read `logs/run.log`.
