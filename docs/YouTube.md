# YouTube Shorts Automater

MPV2 uses a similar implementation of V1 (see [MPV1](https://github.com/FujiwaraChoki/MoneyPrinter)), to generate Video-Files and upload them to YouTube Shorts.

In contrast to V1, V2 uses AI generated images as the visuals for the video, instead of using stock footage. This makes the videos more unique and less likely to be flagged by YouTube. V2 also supports music right from the get-go.

When creating a YouTube account in the app, set a `character/context` for the channel as well. This helps the generated topic, script, metadata, and visuals stay aligned with the same channel identity over time.
You can also set a `dialect` for the account so the script, metadata, and TTS stay in the same regional style.

## Relevant Configuration

In your `config.json`, you need the following attributes filled out, so that the bot can function correctly.

```json
{
  "firefox_profile": "The path to your Firefox profile (used to log in to YouTube)",
  "headless": true,
  "llm_provider": "openai",
  "openai_model": "gpt-5-mini",
  "image_provider": "openai",
  "openai_image_model": "gpt-image-1",
  "openrouter_image_model": "black-forest-labs/flux.2-flex",
  "youtube_metadata_model": "",
  "threads": 4,
  "is_for_kids": true
}
```

Metadata generation now produces:
- title
- description
- hashtags
- tags/keywords

If you leave `youtube_metadata_model` empty while using OpenAI, MPV2 automatically uses `gpt-5-nano` for this metadata step to keep credit usage lower.

If you want to use OpenRouter for images instead of OpenAI or Nano Banana, set:

```json
{
  "image_provider": "openrouter",
  "openrouter_api_key": "your_openrouter_api_key",
  "openrouter_image_model": "black-forest-labs/flux.2-flex"
}
```

## Roadmap

Here are some features that are planned for the future:

- [ ] Subtitles (using either AssemblyAI or locally assembling them)
