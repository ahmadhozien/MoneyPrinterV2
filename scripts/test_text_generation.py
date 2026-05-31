"""
Quick, cheap test for the text-generation (LLM) stages of the YouTube pipeline.

Runs ONLY topic -> script -> metadata -> image prompts (no image generation,
no TTS, no video render, no browser/upload), then prints token usage and the
estimated cost so you can validate the token-reduction changes.

Run from the project root:

    python scripts/test_text_generation.py
    python scripts/test_text_generation.py "funny cat facts" English

Requires the same config.json the app uses (llm_provider + model + key).
"""

import os
import sys

# Windows consoles default to cp1252 and choke on the status emojis; force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# Make bare `from config import ...` style imports work, like the app does.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))

from config import get_llm_provider, get_configured_llm_model  # noqa: E402
from llm_provider import select_model  # noqa: E402
from classes.YouTube import YouTube  # noqa: E402


def main() -> None:
    niche = sys.argv[1] if len(sys.argv) > 1 else "surprising science facts"
    language = sys.argv[2] if len(sys.argv) > 2 else "English"

    provider = get_llm_provider()
    model = get_configured_llm_model()
    if not model:
        print("No LLM model configured. Set openai_model / ollama_model in config.json.")
        return
    select_model(model, provider)
    print(f"Provider: {provider}   Model: {model}")
    print(f"Niche: {niche!r}   Language: {language!r}\n")

    # open_browser=False => no Firefox, no Selenium.
    yt = YouTube(
        account_uuid="test-text-gen",
        account_nickname="test",
        fp_profile_path="",
        niche=niche,
        language=language,
        open_browser=False,
    )

    print("1/4 generate_topic ...")
    print("   ->", yt.generate_topic(), "\n")

    print("2/4 generate_script ...")
    print("   ->", yt.generate_script(), "\n")

    print("3/4 generate_metadata ...")
    print("   ->", yt.generate_metadata(), "\n")

    print("4/4 generate_prompts ...")
    prompts = yt.generate_prompts()
    for i, p in enumerate(prompts):
        print(f"   [{i}] {p}")
    print()

    # Summarize the text-generation ledger the app already records.
    text_items = [c for c in yt._cost_items if c.get("category") == "text_generation"]
    total_in = sum(c["details"].get("input_tokens", 0) for c in text_items)
    total_out = sum(c["details"].get("output_tokens", 0) for c in text_items)
    total_cost = sum(c.get("estimated_cost", 0.0) for c in text_items)

    print("=" * 48)
    print(f"LLM text calls : {len(text_items)}")
    print(f"Input tokens   : {total_in:,}")
    print(f"Output tokens  : {total_out:,}")
    print(f"Total tokens   : {total_in + total_out:,}")
    print(f"Est. cost (USD): ${total_cost:.4f}")
    print("=" * 48)
    print(
        "\nTip: set youtube_combine_metadata_and_prompts=false in config.json and"
        "\nre-run to compare call count / tokens against the combined path."
    )


if __name__ == "__main__":
    main()
