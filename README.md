# MoneyPrinter V2 — ahmadhozien fork

> 🍴 **This is a modified fork** of [FujiwaraChoki/MoneyPrinterV2](https://github.com/FujiwaraChoki/MoneyPrinterV2),
> maintained by [@ahmadhozien](https://github.com/ahmadhozien). It is distributed under the same
> **AGPL-3.0** license as the original. See [Changes in this fork](#changes-in-this-fork) below.

Sponsored by Post Bridge

<a href="https://post-bridge.com?atp=MoneyPrinter">
  <img src="docs/repo/PostBridgeBanner.png" alt="Post Bridge integration banner" width="720" />
</a>


[![Based on FujiwaraChoki/MoneyPrinterV2](https://img.shields.io/badge/based_on-FujiwaraChoki%2FMoneyPrinterV2-blue?style=for-the-badge&logo=github)](https://github.com/FujiwaraChoki/MoneyPrinterV2)

[![GitHub license](https://img.shields.io/github/license/ahmadhozien/MoneyPrinterV2?style=for-the-badge)](https://github.com/ahmadhozien/MoneyPrinterV2/blob/main/LICENSE)
[![GitHub issues](https://img.shields.io/github/issues/ahmadhozien/MoneyPrinterV2?style=for-the-badge)](https://github.com/ahmadhozien/MoneyPrinterV2/issues)
[![GitHub stars](https://img.shields.io/github/stars/ahmadhozien/MoneyPrinterV2?style=for-the-badge)](https://github.com/ahmadhozien/MoneyPrinterV2/stargazers)

An Application that automates the process of making money online.
MPV2 (MoneyPrinter Version 2) is, as the name suggests, the second version of the MoneyPrinter project. It is a complete rewrite of the original project, with a focus on a wider range of features and a more modular architecture.

> **Note:** MPV2 needs Python 3.12 to function effectively.
> Watch the YouTube video [here](https://youtu.be/wAZ_ZSuIqfk)

## Features

- [x] **YouTube Shorts automator** — full pipeline: LLM script → TTS voiceover → AI images / stock
      footage → MoviePy composite with word-by-word subtitles and sound effects → Selenium upload
      (with CRON Jobs => `scheduler`)
- [x] **TikTok uploader** for the generated short-form videos
- [x] **Twitter/X bot** (with CRON Jobs => `scheduler`)
- [x] **Affiliate Marketing** (Amazon + Twitter)
- [x] **Local business outreach** — scrape Google Maps, extract emails, send cold outreach
- [x] **Cross-posting** to TikTok / Instagram via [Post Bridge](https://www.post-bridge.com)
- [x] **Streamlit GUI** dashboard (`app_gui.py`) in addition to the CLI
- [x] **Multi-provider** support for LLM, image, TTS, and STT (see [Configuration](#configuration))
- [x] **Bilingual subtitles** (English + Arabic) and per-keyword sound effects
- [x] **Cost tracking** for paid API providers

## Versions

MoneyPrinter has different versions for multiple languages developed by the community for the community. Here are some known versions:

- Chinese: [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)

If you would like to submit your own version/fork of MoneyPrinter, please open an issue describing the changes you made to the fork.

## Changes in this fork

This fork (by [@ahmadhozien](https://github.com/ahmadhozien)) adds the following on top of upstream
[FujiwaraChoki/MoneyPrinterV2](https://github.com/FujiwaraChoki/MoneyPrinterV2):

- **TikTok uploader** for the generated short-form videos, with supporting scripts.
- **Multiple LLM/image/TTS/STT providers** — OpenAI (LLM, image, TTS), OpenRouter and Pixabay for
  visuals, Nano Banana 2 (Gemini) image generation, local Whisper or AssemblyAI for subtitles.
  All cloud providers are called over plain HTTP, so no extra vendor SDKs are required.
- **YouTube pipeline improvements** — cultural-safety filtering, smarter asset selection
  (`asset_strategy`: AI images, stock footage, or mixed), and a configurable target duration.
- **Bilingual subtitles** — separate English and Arabic fonts with word-by-word rendering.
- **Sound effects engine** — maps trigger words to SFX clips, with volume/offset controls.
- **Cost tracking** — per-provider pricing in `config.json` to estimate run costs.
- **Streamlit GUI** (`app_gui.py`) with Windows launcher scripts (`run_gui.bat`, `run_gui.ps1`).
- **Firefox profile lock fix** across all automation classes (YouTube, Twitter, AFM, TikTok) — you
  can now run automation while a *different* Firefox profile is open in another window.

## Prerequisites

Install these on your system **before** the Python steps below:

| Tool | Why it's needed | Required? |
|---|---|---|
| **Python 3.12** | Runs the whole app | ✅ Always |
| **FFmpeg** | Video encoding/decoding for MoviePy | ✅ Always |
| **ImageMagick** | Renders subtitle text onto video frames | ✅ Always |
| **Mozilla Firefox** + a profile already logged in to YouTube/X/TikTok | Selenium uploads (the app never logs in for you) | ✅ For any upload |
| **[Ollama](https://ollama.com)** + a pulled model (e.g. `ollama pull llama3`) | Local LLM text generation | ⚠️ Only if `llm_provider` is `ollama` |
| **[Go](https://go.dev/dl/)** | Google Maps scraper for the outreach feature | ⚠️ Only for outreach |

> macOS users: `bash scripts/setup_local.sh` auto-configures Ollama, ImageMagick, and a Firefox
> profile. Then run `python scripts/preflight_local.py` to verify services are reachable.

## Installation

```bash
# 1. Clone this fork
git clone https://github.com/ahmadhozien/MoneyPrinterV2.git
cd MoneyPrinterV2

# 2. Copy the example config — you'll fill it in next (see Configuration below)
cp config.example.json config.json          # Windows PowerShell: copy config.example.json config.json

# 3. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\activate                      # Windows
source venv/bin/activate                     # macOS / Linux

# 4. Install Python dependencies
pip install -r requirements.txt
```

## Configuration

All settings live in `config.json` at the project root (copied from `config.example.json`). The app
re-reads this file on every run, so you can edit it without restarting. Key fields:

**Browser (required for uploads)**
- `firefox_profile` — absolute path to a Firefox profile already logged in to your target platforms.
- `headless` — run the browser without a visible window.

**LLM text generation** — set `llm_provider` to one of:
- `ollama` (default, free, local) — also set `ollama_base_url` and `ollama_model` (leave `ollama_model`
  empty to pick from installed models at startup).
- `openai` — set `openai_api_key`, `openai_model`, and optionally `openai_base_url`.

**Image / video assets** — `image_provider` + `asset_strategy` (`ai`, `stock`, or `mixed`):
- `nanobanana2` (Gemini) — set `nanobanana2_api_key`.
- `openai` images — set `openai_api_key` (uses `openai_image_model`).
- `openrouter` — set `openrouter_api_key`.
- `pixabay` stock footage — set `pixabay_api_key`.

**Voiceover (TTS)** — `tts_provider`: `auto`/`kitten` (free, local) or `openai` (`openai_tts_model`, `openai_tts_voice`).

**Subtitles (STT)** — `stt_provider`: `local_whisper` (free) or `third_party_assemblyai` (`assembly_ai_api_key`).

**Subtitles & SFX** — `subtitle_font_english`, `subtitle_font_arabic`, `subtitle_color`,
`sound_effects_enabled`, and the `sound_effects` keyword→clip map.

**Outreach** — `email` SMTP block, `google_maps_scraper_niche`, `outreach_message_*`.

**Cross-posting** — `post_bridge` block (`enabled`, `api_key`, `platforms`, `auto_crosspost`).

> 🔑 **API keys** can also be supplied via environment variables instead of `config.json`:
> `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `PIXABAY_API_KEY`, and
> `POST_BRIDGE_API_KEY` are used as fallbacks when the matching config value is empty.
> See [docs/Configuration.md](docs/Configuration.md) for the full reference.

## Usage

Run the interactive CLI from the **project root** (it adds `src/` to the path):

```bash
python src/main.py
```

For headless / scheduled runs, the scheduler invokes:

```bash
python src/cron.py <platform> <account_uuid>
```

## GUI

You can also run the first Streamlit-based GUI:

```bash
streamlit run app_gui.py
```

On Windows, you can also use the included launcher scripts:

```bash
run_gui.bat
```

or:

```powershell
.\run_gui.ps1
```

## Documentation

All relevant documents can be found [here](docs/).

## Scripts

For easier usage, there are some scripts in the `scripts` directory that can be used to directly access the core functionality of MPV2 without the need for user interaction.

All scripts need to be run from the root directory of the project, e.g. `bash scripts/upload_video.sh`.

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct, and the process for submitting pull requests to us. Check out [docs/Roadmap.md](docs/Roadmap.md) for a list of features that need to be implemented.

## Code of Conduct

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for details on our code of conduct, and the process for submitting pull requests to us.

## License

MoneyPrinterV2 is licensed under `Affero General Public License v3.0`. See [LICENSE](LICENSE) for more information.

## Acknowledgments

- [KittenTTS](https://github.com/KittenML/KittenTTS)
- [gpt4free](https://github.com/xtekky/gpt4free)

## Disclaimer

This project is for educational purposes only. The author will not be responsible for any misuse of the information provided. All the information on this website is published in good faith and for general information purposes only. The author does not make any warranties about the completeness, reliability, and accuracy of this information. Any action you take upon the information you find on this website (FujiwaraChoki/MoneyPrinterV2) is strictly at your own risk. The author will not be liable for any losses and/or damages in connection with the use of our website.
