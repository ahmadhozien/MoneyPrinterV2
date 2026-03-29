import re
import base64
import json
import time
import os
import io
import hashlib
import requests
import assemblyai as aai
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from utils import *
from cache import *
from .Tts import TTS
from llm_provider import generate_text, generate_text_result
from config import *
from status import *
from uuid import uuid4
from constants import *
from typing import List
from moviepy.editor import *
from termcolor import colored
from selenium_firefox import *
from selenium import webdriver
from moviepy.video.fx.all import crop
from moviepy.config import change_settings
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from webdriver_manager.firefox import GeckoDriverManager
from datetime import datetime

if not hasattr(Image, "ANTIALIAS") and hasattr(Image, "Resampling"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

# Set ImageMagick Path
change_settings({"IMAGEMAGICK_BINARY": get_imagemagick_path()})

DEFAULT_MIN_IMAGE_PROMPTS = 10
DEFAULT_MAX_IMAGE_PROMPTS = 12
IMAGE_RATE_LIMIT_RETRIES = 3
IMAGE_RATE_LIMIT_BACKOFF_SECONDS = 5


class ImageRateLimitError(RuntimeError):
    """
    Raised when the configured image provider is rate-limited and the request
    should stop instead of continuing through the remaining prompts.
    """


def _is_retryable_rate_limit(response: requests.Response) -> bool:
    """
    Returns whether the HTTP response indicates a retryable rate-limit case.

    Args:
        response (requests.Response): provider response

    Returns:
        is_retryable (bool): True if we should stop and report rate limiting
    """
    return response.status_code == 429


def _nanobanana_response_has_no_image(body: dict) -> bool:
    """
    Returns whether Gemini/Nano Banana responded successfully but did not
    include an image payload.

    Args:
        body (dict): provider response JSON

    Returns:
        no_image (bool): True when a candidate explicitly finished with NO_IMAGE
    """
    for candidate in body.get("candidates", []):
        if str(candidate.get("finishReason", "")).upper() == "NO_IMAGE":
            return True
    return False


class YouTube:
    """
    Class for YouTube Automation.

    Steps to create a YouTube Short:
    1. Generate a topic [DONE]
    2. Generate a script [DONE]
    3. Generate metadata (Title, Description, Tags) [DONE]
    4. Generate AI Image Prompts [DONE]
    4. Generate Images based on generated Prompts [DONE]
    5. Convert Text-to-Speech [DONE]
    6. Show images each for n seconds, n: Duration of TTS / Amount of images [DONE]
    7. Combine Concatenated Images with the Text-to-Speech [DONE]
    """

    def __init__(
        self,
        account_uuid: str,
        account_nickname: str,
        fp_profile_path: str,
        niche: str,
        language: str,
        dialect: str = "",
        character_context: str = "",
        is_for_kids: bool | None = None,
        open_browser: bool = True,
    ) -> None:
        """
        Constructor for YouTube Class.

        Args:
            account_uuid (str): The unique identifier for the YouTube account.
            account_nickname (str): The nickname for the YouTube account.
            fp_profile_path (str): Path to the firefox profile that is logged into the specificed YouTube Account.
            niche (str): The niche of the provided YouTube Channel.
            language (str): The language of the Automation.

        Returns:
            None
        """
        self._account_uuid: str = account_uuid
        self._account_nickname: str = account_nickname
        self._fp_profile_path: str = fp_profile_path
        self._niche: str = niche
        self._language: str = language
        self._dialect: str = dialect.strip()
        self._character_context: str = character_context.strip()
        self._is_for_kids: bool = get_is_for_kids() if is_for_kids is None else bool(is_for_kids)
        self.browser: webdriver.Firefox | None = None
        self._workspace_dir: str | None = None

        self.images = []
        self.visual_assets = []
        self.scene_units = []
        self._cost_items = []
        self._cost_notes = []
        self._pixabay_selection_debug = []

        # Initialize the Firefox profile
        self.options: Options = Options()

        # Set headless state of browser
        if get_headless():
            self.options.add_argument("--headless")

        if open_browser:
            if not os.path.isdir(self._fp_profile_path):
                raise ValueError(
                    f"Firefox profile path does not exist or is not a directory: {self._fp_profile_path}"
                )

            self._assert_profile_is_available(self._fp_profile_path)

            self.options.add_argument("-profile")
            self.options.add_argument(self._fp_profile_path)

            # Set the service
            self.service: Service = Service(GeckoDriverManager().install())

            # Initialize the browser
            self.browser = webdriver.Firefox(
                service=self.service, options=self.options
            )

    def _assert_profile_is_available(self, profile_path: str) -> None:
        """
        Ensures the Firefox profile is not currently locked by another running
        Firefox process before Selenium tries to launch it.

        Args:
            profile_path (str): Firefox profile folder

        Returns:
            None
        """
        lock_path = os.path.join(profile_path, "parent.lock")
        if not os.path.exists(lock_path):
            return

        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq firefox.exe"],
                capture_output=True,
                text=True,
                check=False,
            )
            firefox_running = "firefox.exe" in result.stdout.lower()
        except Exception:
            firefox_running = True

        if firefox_running:
            raise RuntimeError(
                "The selected Firefox profile is currently in use. Close Firefox completely and try again."
            )

    @property
    def niche(self) -> str:
        """
        Getter Method for the niche.

        Returns:
            niche (str): The niche
        """
        return self._niche

    @property
    def language(self) -> str:
        """
        Getter Method for the language to use.

        Returns:
            language (str): The language
        """
        return self._language

    @property
    def dialect(self) -> str:
        """
        Getter Method for the dialect/style to use.

        Returns:
            dialect (str): The dialect/style
        """
        return self._dialect

    def generate_response(self, prompt: str, model_name: str = None) -> str:
        """
        Generates an LLM Response based on a prompt and the user-provided model.

        Args:
            prompt (str): The prompt to use in the text generation.

        Returns:
            response (str): The generated AI Repsonse.
        """
        result = generate_text_result(prompt, model_name=model_name)
        self._record_text_generation_cost(
            provider=result.get("provider", get_llm_provider()),
            model=result.get("model", model_name or get_configured_llm_model()),
            usage=result.get("usage", {}),
        )
        return result["text"]

    def _get_pricing_currency(self) -> str:
        """
        Returns the configured report currency.

        Returns:
            currency (str): pricing currency label
        """
        pricing = get_pricing_config()
        return str(pricing.get("currency", "USD") or "USD")

    def _get_pricing_model_config(
        self,
        category: str,
        provider: str,
        model_name: str | None = None,
    ) -> dict:
        """
        Resolves model-specific pricing config for a provider/category pair.

        Args:
            category (str): pricing section
            provider (str): provider name
            model_name (str | None): model identifier

        Returns:
            entry (dict): pricing entry or empty dict
        """
        pricing = get_pricing_config()
        category_cfg = pricing.get(category, {}) if isinstance(pricing, dict) else {}
        provider_cfg = category_cfg.get(str(provider or "").strip().lower(), {})
        models_cfg = provider_cfg.get("models", {}) if isinstance(provider_cfg, dict) else {}
        normalized_model = str(model_name or "").strip()

        if normalized_model and normalized_model in models_cfg:
            return models_cfg[normalized_model]

        lowered_lookup = {
            str(key).strip().lower(): value
            for key, value in models_cfg.items()
            if isinstance(key, str)
        }
        if normalized_model and normalized_model.lower() in lowered_lookup:
            return lowered_lookup[normalized_model.lower()]

        return models_cfg.get("default", {}) if isinstance(models_cfg, dict) else {}

    def _add_cost_note(self, note: str) -> None:
        """
        Adds a human-readable note to the current pricing report.

        Args:
            note (str): note text

        Returns:
            None
        """
        cleaned = str(note or "").strip()
        if cleaned and cleaned not in self._cost_notes:
            self._cost_notes.append(cleaned)

    def _record_cost_item(
        self,
        *,
        category: str,
        provider: str,
        model: str,
        item_type: str,
        estimated_cost: float,
        quantity: float,
        unit: str,
        details: dict | None = None,
        label: str | None = None,
    ) -> None:
        """
        Appends one normalized cost entry to the current run ledger.

        Args:
            category (str): pricing category
            provider (str): provider name
            model (str): model or service identifier
            item_type (str): granular item type
            estimated_cost (float): estimated USD amount
            quantity (float): measured quantity
            unit (str): quantity unit
            details (dict | None): extra context
            label (str | None): optional display label

        Returns:
            None
        """
        self._cost_items.append(
            {
                "category": category,
                "provider": str(provider or ""),
                "model": str(model or ""),
                "type": str(item_type or ""),
                "label": str(label or item_type or category),
                "estimated_cost": round(max(0.0, float(estimated_cost or 0.0)), 6),
                "quantity": round(max(0.0, float(quantity or 0.0)), 6),
                "unit": str(unit or ""),
                "details": details or {},
            }
        )

    def _record_text_generation_cost(
        self,
        *,
        provider: str,
        model: str,
        usage: dict | None = None,
    ) -> None:
        """
        Records the estimated cost of one text-generation call.

        Args:
            provider (str): provider name
            model (str): model name
            usage (dict | None): token usage payload

        Returns:
            None
        """
        usage = usage or {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)

        pricing_entry = self._get_pricing_model_config(
            "text_generation",
            provider,
            model,
        )
        input_rate = float(pricing_entry.get("input_per_1m_tokens", 0.0) or 0.0)
        output_rate = float(pricing_entry.get("output_per_1m_tokens", 0.0) or 0.0)
        estimated_cost = ((input_tokens / 1_000_000) * input_rate) + (
            (output_tokens / 1_000_000) * output_rate
        )

        if str(provider or "").strip().lower() == "ollama":
            self._add_cost_note(
                "Ollama text-generation cost is estimated as zero by default. "
                "If your Ollama cloud model is billable, override pricing.models.ollama in config.json."
            )

        self._record_cost_item(
            category="text_generation",
            provider=provider,
            model=model,
            item_type="llm_call",
            label="Text generation",
            estimated_cost=estimated_cost,
            quantity=input_tokens + output_tokens,
            unit="tokens",
            details={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "input_rate_per_1m": input_rate,
                "output_rate_per_1m": output_rate,
            },
        )

    def _record_image_generation_cost(self, provider: str, model: str, scene_index: int | None = None) -> None:
        """
        Records the estimated cost of one successful generated image.

        Args:
            provider (str): image provider
            model (str): model name
            scene_index (int | None): optional scene index

        Returns:
            None
        """
        pricing_entry = self._get_pricing_model_config(
            "image_generation",
            provider,
            model,
        )
        quality = get_openai_image_quality() if str(provider).lower() == "openai" else ""
        estimated_cost = 0.0
        if quality and isinstance(pricing_entry.get("qualities"), dict):
            estimated_cost = float(pricing_entry["qualities"].get(quality, 0.0) or 0.0)
        else:
            estimated_cost = float(pricing_entry.get("per_image", 0.0) or 0.0)

        self._record_cost_item(
            category="image_generation",
            provider=provider,
            model=model,
            item_type="generated_image",
            label="AI image",
            estimated_cost=estimated_cost,
            quantity=1,
            unit="image",
            details={
                "scene_index": scene_index,
                "quality": quality or None,
            },
        )

    def _record_stock_asset_cost(self, provider: str, asset_type: str, scene_index: int | None = None) -> None:
        """
        Records one sourced stock asset, typically free.

        Args:
            provider (str): stock provider
            asset_type (str): image or video
            scene_index (int | None): optional scene index

        Returns:
            None
        """
        pricing_entry = self._get_pricing_model_config(
            "image_generation",
            provider,
            "default",
        )
        estimated_cost = float(
            pricing_entry.get("per_asset", pricing_entry.get("per_image", 0.0)) or 0.0
        )
        self._record_cost_item(
            category="image_generation",
            provider=provider,
            model="stock",
            item_type=f"stock_{asset_type}",
            label=f"Stock {asset_type}",
            estimated_cost=estimated_cost,
            quantity=1,
            unit=asset_type,
            details={"scene_index": scene_index},
        )

    def _record_tts_cost(self, payload: dict) -> None:
        """
        Records TTS usage from the active speech provider.

        Args:
            payload (dict): callback payload from TTS class

        Returns:
            None
        """
        provider = str(payload.get("provider", "kitten")).strip().lower() or "kitten"
        model = str(payload.get("model", "")).strip() or "default"
        audio_seconds = max(0.0, float(payload.get("audio_seconds", 0.0) or 0.0))
        audio_minutes = audio_seconds / 60 if audio_seconds > 0 else 0.0

        pricing_entry = self._get_pricing_model_config("tts", provider, model)
        per_minute_rate = float(pricing_entry.get("per_minute_audio", 0.0) or 0.0)

        self._record_cost_item(
            category="tts",
            provider=provider,
            model=model,
            item_type="speech_generation",
            label="Voiceover",
            estimated_cost=audio_minutes * per_minute_rate,
            quantity=audio_minutes,
            unit="audio_minutes",
            details={
                "audio_seconds": round(audio_seconds, 3),
                "voice": payload.get("voice"),
                "characters": payload.get("characters"),
                "rate_per_minute_audio": per_minute_rate,
            },
        )

    def _record_stt_cost(
        self,
        provider: str,
        audio_path: str,
        model: str = "default",
        label: str = "Subtitles",
    ) -> None:
        """
        Records subtitle/STT cost from the audio duration.

        Args:
            provider (str): subtitle provider key
            audio_path (str): audio file path
            model (str): model name
            label (str): display label

        Returns:
            None
        """
        audio_clip = AudioFileClip(audio_path)
        try:
            audio_seconds = max(0.0, float(audio_clip.duration or 0.0))
        finally:
            audio_clip.close()

        pricing_entry = self._get_pricing_model_config("stt", provider, model)
        audio_minutes = audio_seconds / 60 if audio_seconds > 0 else 0.0
        per_minute_rate = float(pricing_entry.get("per_minute_audio", 0.0) or 0.0)

        self._record_cost_item(
            category="stt",
            provider=provider,
            model=model,
            item_type="subtitle_generation",
            label=label,
            estimated_cost=audio_minutes * per_minute_rate,
            quantity=audio_minutes,
            unit="audio_minutes",
            details={
                "audio_seconds": round(audio_seconds, 3),
                "rate_per_minute_audio": per_minute_rate,
            },
        )

    def get_pricing_report(self) -> dict:
        """
        Builds the current pricing report for the run.

        Returns:
            report (dict): pricing summary and itemized entries
        """
        summary: dict[str, dict] = {}
        total_estimated_cost = 0.0

        for item in self._cost_items:
            category = item["category"]
            total_estimated_cost += float(item.get("estimated_cost", 0.0) or 0.0)
            bucket = summary.setdefault(
                category,
                {"estimated_cost": 0.0, "entries": 0, "quantity": 0.0, "units": []},
            )
            bucket["estimated_cost"] += float(item.get("estimated_cost", 0.0) or 0.0)
            bucket["entries"] += 1
            bucket["quantity"] += float(item.get("quantity", 0.0) or 0.0)
            unit = str(item.get("unit", "") or "")
            if unit and unit not in bucket["units"]:
                bucket["units"].append(unit)

        normalized_summary = {
            category: {
                "estimated_cost": round(values["estimated_cost"], 6),
                "entries": values["entries"],
                "quantity": round(values["quantity"], 6),
                "units": values["units"],
            }
            for category, values in summary.items()
        }

        return {
            "currency": self._get_pricing_currency(),
            "total_estimated_cost": round(total_estimated_cost, 6),
            "items": list(self._cost_items),
            "summary": normalized_summary,
            "notes": list(self._cost_notes),
        }

    def _write_pricing_report(self) -> dict:
        """
        Persists the current run pricing report into the workspace.

        Returns:
            report (dict): pricing report
        """
        report = self.get_pricing_report()
        report["workspace_dir"] = self._get_workspace_dir()
        report["video_path"] = getattr(self, "video_path", "")
        report["subject"] = getattr(self, "subject", "")
        report["language"] = self.language
        report["dialect"] = self.dialect

        pricing_path = self._get_workspace_path("pricing.json")
        with open(pricing_path, "w", encoding="utf-8") as file:
            json.dump(report, file, indent=2, ensure_ascii=False)

        return report

    def _slugify_label(self, value: str) -> str:
        """
        Converts a label into a filesystem-friendly slug.

        Args:
            value (str): raw label

        Returns:
            slug (str): safe slug
        """
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip())
        slug = slug.strip("-_")
        return slug or "youtube"

    def _get_workspace_dir(self) -> str:
        """
        Returns the per-run workspace directory for generated assets.

        Returns:
            workspace_dir (str): absolute workspace path
        """
        if self._workspace_dir is None:
            runs_root = os.path.join(ROOT_DIR, ".mp", "youtube_runs")
            os.makedirs(runs_root, exist_ok=True)
            run_name = (
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
                f"{self._slugify_label(self._account_nickname)}_"
                f"{str(uuid4())[:8]}"
            )
            self._workspace_dir = os.path.join(runs_root, run_name)
            os.makedirs(self._workspace_dir, exist_ok=True)

        return self._workspace_dir

    def _get_workspace_path(self, filename: str) -> str:
        """
        Builds an asset path inside the current workspace directory.

        Args:
            filename (str): target filename

        Returns:
            path (str): absolute asset path
        """
        return os.path.join(self._get_workspace_dir(), filename)

    def _get_metadata_model_name(self) -> str | None:
        """
        Returns a cheaper model override for metadata generation when available.

        Returns:
            model_name (str | None): model override
        """
        configured_model = get_youtube_metadata_model().strip()
        if configured_model:
            return configured_model

        if get_llm_provider() == "openai":
            return "gpt-5-nano"

        return None

    def _get_character_context_block(self) -> str:
        """
        Returns an optional prompt block that keeps generations aligned with
        the account's persistent character and voice.

        Returns:
            block (str): prompt block or empty string
        """
        if not self._character_context:
            return ""

        return (
            f"Channel character context: {self._character_context}\n"
            "Keep the topic, script, visuals, and metadata aligned with this channel identity.\n"
        )

    def _get_locale_block(self) -> str:
        """
        Returns a prompt block describing the content language and dialect.

        Returns:
            block (str): locale instructions
        """
        dialect_line = f"Dialect/Style: {self.dialect}\n" if self.dialect else ""
        return (
            f"Language: {self.language}\n"
            f"{dialect_line}"
            "If a dialect/style is specified, write naturally in that dialect while keeping the text fluent and native.\n"
        )

    def _get_visual_locale_hint(self) -> str:
        """
        Returns a concise English hint describing the real-world cultural
        setting that the visuals should reflect.

        Returns:
            hint (str): visual locale hint or empty string
        """
        normalized_language = str(self.language or "").strip().lower()
        normalized_dialect = str(self.dialect or "").strip().lower()

        if "egyptian" in normalized_dialect:
            return (
                "Egyptian setting, Egyptian people, Cairo or Egyptian urban life, "
                "Arabic-speaking environment, culturally authentic details"
            )

        if any(term in normalized_dialect for term in ("gulf", "saudi")):
            return (
                "Gulf Arab setting, Arab people, Arabic-speaking environment, "
                "culturally authentic Gulf details"
            )

        if "levantine" in normalized_dialect:
            return (
                "Levantine Arab setting, Arab people, Arabic-speaking environment, "
                "culturally authentic Levant details"
            )

        if any(term in normalized_dialect for term in ("darija", "moroccan")):
            return (
                "Moroccan setting, Moroccan people, North African Arabic-speaking environment, "
                "culturally authentic Moroccan details"
            )

        if "arabic" in normalized_language or "arabic" in normalized_dialect:
            return (
                "Arab setting, Arab people, Arabic-speaking environment, "
                "culturally authentic local details"
            )

        return ""

    def _get_visual_locale_prompt_block(self) -> str:
        """
        Returns prompt instructions that keep visuals culturally aligned with
        the chosen audience while leaving the prompt text itself in English.

        Returns:
            block (str): prompt block or empty string
        """
        hint = self._get_visual_locale_hint()
        if not hint:
            return ""

        return (
            f"Visual locale: {hint}.\n"
            "Write the prompt in English, but keep the people, places, street details, and atmosphere aligned with that locale.\n"
        )

    def _get_target_duration_block(self) -> str:
        """
        Returns a prompt block that steers the generated script toward a target
        spoken runtime.

        Returns:
            block (str): duration instructions or empty string
        """
        target_duration = get_youtube_target_duration_seconds()
        if target_duration <= 0:
            return ""

        approx_word_budget = max(20, int(round(target_duration * 2.4)))
        return (
            f"Target spoken runtime: about {target_duration} seconds.\n"
            f"Aim for roughly {approx_word_budget} words total when read aloud naturally.\n"
            "Keep the pacing tight and concise enough to stay around that runtime.\n"
        )

    def generate_topic(self) -> str:
        """
        Generates a topic based on the YouTube Channel niche.

        Returns:
            topic (str): The generated topic.
        """
        completion = self.generate_response(
            f"{self._get_character_context_block()}"
            f"{self._get_locale_block()}"
            f"Please generate a specific video idea that takes about the following topic: {self.niche}. "
            "Make it exactly one sentence. Only return the topic, nothing else."
        )

        if not completion:
            error("Failed to generate Topic.")

        self.subject = completion

        return completion

    def generate_script(self) -> str:
        """
        Generate a script for a video, depending on the subject of the video, the number of paragraphs, and the AI model.

        Returns:
            script (str): The script of the video.
        """
        sentence_length = get_script_sentence_length()
        prompt = f"""
        Generate a script for a video in {sentence_length} sentences, depending on the subject of the video.

        The script is to be returned as a string with the specified number of paragraphs.

        Here is an example of a string:
        "This is an example string."

        Do not under any circumstance reference this prompt in your response.

        Get straight to the point, don't start with unnecessary things like, "welcome to this video".

        Obviously, the script should be related to the subject of the video.
        
        YOU MUST NOT EXCEED THE {sentence_length} SENTENCES LIMIT. MAKE SURE THE {sentence_length} SENTENCES ARE SHORT.
        YOU MUST NOT INCLUDE ANY TYPE OF MARKDOWN OR FORMATTING IN THE SCRIPT, NEVER USE A TITLE.
        YOU MUST WRITE THE SCRIPT IN THE LANGUAGE AND DIALECT SPECIFIED BELOW.
        ONLY RETURN THE RAW CONTENT OF THE SCRIPT. DO NOT INCLUDE "VOICEOVER", "NARRATOR" OR SIMILAR INDICATORS OF WHAT SHOULD BE SPOKEN AT THE BEGINNING OF EACH PARAGRAPH OR LINE. YOU MUST NOT MENTION THE PROMPT, OR ANYTHING ABOUT THE SCRIPT ITSELF. ALSO, NEVER TALK ABOUT THE AMOUNT OF PARAGRAPHS OR LINES. JUST WRITE THE SCRIPT
        {self._get_character_context_block()}
        {self._get_locale_block()}
        {self._get_target_duration_block()}
        
        Subject: {self.subject}
        """
        completion = self.generate_response(prompt)

        # Apply regex to remove *
        completion = re.sub(r"\*", "", completion)

        if not completion:
            error("The generated script is empty.")
            return

        if len(completion) > 5000:
            if get_verbose():
                warning("Generated Script is too long. Retrying...")
            return self.generate_script()

        self.script = completion
        self.scene_units = []

        return completion

    def _derive_subject_from_script(self, script: str) -> str:
        """
        Derives a usable topic/subject line from a provided manual script.

        Args:
            script (str): Manual script text

        Returns:
            subject (str): Derived subject line
        """
        cleaned_script = re.sub(r"\s+", " ", script).strip()
        first_sentence = re.split(r"[.!?؟\n]+", cleaned_script)[0].strip()
        derived = first_sentence or cleaned_script
        return derived[:120].strip()

    def set_manual_script(self, script: str, subject: str = "") -> None:
        """
        Uses a caller-provided script instead of generating one with the LLM.

        Args:
            script (str): Final narration script
            subject (str): Optional topic/subject override

        Returns:
            None
        """
        cleaned_script = str(script or "").strip()
        if not cleaned_script:
            raise RuntimeError("Manual script cannot be empty.")

        cleaned_subject = str(subject or "").strip()
        self.subject = cleaned_subject or self._derive_subject_from_script(cleaned_script)
        self.script = cleaned_script
        self.scene_units = []

    def generate_metadata(self) -> dict:
        """
        Generates SEO metadata for the to-be-uploaded YouTube Short.

        Returns:
            metadata (dict): The generated metadata.
        """
        prompt = f"""
        Generate YouTube Shorts metadata as a JSON object.
        {self._get_character_context_block()}
        {self._get_locale_block()}

        Subject: {self.subject}
        Script: {self.script}

        Return ONLY valid JSON with these keys:
        - "title": a natural, clickable title under 80 characters. No markdown.
        - "description": 2 short lines that match the video and encourage engagement.
        - "hashtags": an array of 3 short relevant hashtags without explanations.
        - "tags": an array of 8 short search-friendly tags/keywords relevant to YouTube discoverability.

        Rules:
        - Keep everything aligned with the video's niche, audience, and character context.
        - Use the requested language and dialect naturally.
        - Avoid spammy clickbait, keyword stuffing, or repetitive hashtags.
        - Do not include any text outside the JSON object.
        """

        completion = (
            str(self.generate_response(prompt, model_name=self._get_metadata_model_name()))
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

        try:
            metadata = json.loads(completion)
        except json.JSONDecodeError:
            if get_verbose():
                warning("Metadata response was not valid JSON. Falling back to simpler metadata generation.")

            title = self.generate_response(
                f"{self._get_character_context_block()}{self._get_locale_block()}"
                f"Generate one YouTube Shorts title under 80 characters for this subject: {self.subject}. "
                "Return only the title.",
                model_name=self._get_metadata_model_name(),
            ).strip()
            description = self.generate_response(
                f"{self._get_character_context_block()}{self._get_locale_block()}"
                f"Generate a short YouTube Shorts description for this script: {self.script}. "
                "Return only the description.",
                model_name=self._get_metadata_model_name(),
            ).strip()
            metadata = {
                "title": title,
                "description": description,
                "hashtags": [],
                "tags": [],
            }

        title = str(metadata.get("title", "")).strip()
        description = str(metadata.get("description", "")).strip()
        raw_hashtags = metadata.get("hashtags", [])
        if isinstance(raw_hashtags, str):
            raw_hashtags = re.split(r"[,#\n]+", raw_hashtags)

        raw_tags = metadata.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = re.split(r"[,|\n]+", raw_tags)

        hashtags = [
            str(item).strip().replace(" ", "")
            for item in raw_hashtags
            if str(item).strip()
        ][:3]
        tags = [
            str(item).strip()
            for item in raw_tags
            if str(item).strip()
        ][:8]

        if not title:
            raise RuntimeError("Metadata generation did not return a title.")

        if len(title) > 80:
            title = title[:80].rstrip()

        hashtag_line = " ".join(
            hashtag if hashtag.startswith("#") else f"#{hashtag}"
            for hashtag in hashtags
        ).strip()
        if hashtag_line:
            description = f"{description}\n\n{hashtag_line}".strip()

        self.metadata = {
            "title": title,
            "description": description,
            "hashtags": hashtags,
            "tags": tags,
        }

        return self.metadata

    def generate_prompts(self) -> List[str]:
        """
        Generates AI Image Prompts based on the provided Video Script.

        Returns:
            image_prompts (List[str]): Generated List of image prompts.
        """
        scene_units = self._get_scene_units()
        if not scene_units:
            raise RuntimeError("Could not derive scene units from the current script.")

        n_prompts = len(scene_units)

        prompt = f"""
        Generate exactly {n_prompts} image prompts for AI image generation,
        depending on the subject of a video.
        Subject: {self.subject}
        {self._get_character_context_block()}
        {self._get_visual_locale_prompt_block()}

        The image prompts are to be returned as
        a JSON-Array of strings.

        Each prompt should be one concise still-image description sentence.
        Always write the image prompts in clear English, even if the video language is different.
        Keep each prompt concise and visually direct.
        Keep each prompt between 8 and 18 words when possible.
        Do not number the prompts.
        Always include the main subject of the video.
        Avoid readable text, captions, logos, watermarks, app interfaces, or signs with words.
        Prefer physically depictable cinematic scenes over abstract narration.

        Be emotional and use interesting adjectives to make the
        Image Prompt as detailed as possible.

        YOU MUST ONLY RETURN THE JSON-ARRAY OF STRINGS.
        YOU MUST NOT RETURN ANYTHING ELSE.
        YOU MUST NOT RETURN THE SCRIPT.

        Each prompt must correspond to the matching scene beat below and stay in the same order.
        The search terms must be related to the subject of the video.
        Here is an example of a JSON-Array of strings:
        ["image prompt 1", "image prompt 2", "image prompt 3"]

        Scene beats in order:
        {json.dumps(scene_units, ensure_ascii=False)}
        """

        completion = (
            str(self.generate_response(prompt))
            .replace("```json", "")
            .replace("```", "")
        )

        image_prompts = []

        if "image_prompts" in completion:
            image_prompts = json.loads(completion)["image_prompts"]
        else:
            try:
                image_prompts = json.loads(completion)
                if get_verbose():
                    info(f" => Generated Image Prompts: {image_prompts}")
            except Exception:
                if get_verbose():
                    warning(
                        "LLM returned an unformatted response. Attempting to clean..."
                    )

                # Get everything between [ and ], and turn it into a list
                r = re.compile(r"\[.*\]")
                image_prompts = r.findall(completion)
                if len(image_prompts) == 0:
                    if get_verbose():
                        warning("Failed to generate Image Prompts. Retrying...")
                    return self.generate_prompts()

        if len(image_prompts) == 1 and isinstance(image_prompts[0], str):
            try:
                image_prompts = json.loads(image_prompts[0])
            except Exception:
                pass

        image_prompts = [
            self._make_provider_friendly_image_prompt(prompt)
            for prompt in image_prompts
            if isinstance(prompt, str) and prompt.strip()
        ]

        if self._image_prompts_need_translation(image_prompts):
            image_prompts = [
                self._make_provider_friendly_image_prompt(prompt)
                for prompt in self._translate_image_prompts_to_english(image_prompts)
                if isinstance(prompt, str) and prompt.strip()
            ]

        if len(image_prompts) == 0:
            if get_verbose():
                warning("No usable image prompts returned. Retrying...")
            return self.generate_prompts()

        if len(image_prompts) > n_prompts:
            image_prompts = image_prompts[:n_prompts]

        if len(image_prompts) < n_prompts:
            image_prompts.extend(image_prompts[-1:] * (n_prompts - len(image_prompts)))

        self.image_prompts = image_prompts
        self.scene_units = scene_units

        success(f"Generated {len(image_prompts)} Image Prompts.")

        return image_prompts

    def _get_target_prompt_count(self) -> int:
        """
        Derives a sensible prompt count from the script sentence count.

        Returns:
            prompt_count (int): Number of prompts to request
        """
        sentences = [
            sentence.strip()
            for sentence in re.split(r"[.!?؟\n]+", self.script)
            if sentence.strip()
        ]
        min_prompts = max(1, get_min_image_prompts() or DEFAULT_MIN_IMAGE_PROMPTS)
        max_prompts = max(min_prompts, get_max_image_prompts() or DEFAULT_MAX_IMAGE_PROMPTS)
        sentence_count = len(sentences) or min_prompts
        return max(min_prompts, min(max_prompts, sentence_count))

    def _split_script_into_sentences_for_visuals(self) -> list[str]:
        """
        Splits the script into sentence-like visual beats.

        Returns:
            sentences (list[str]): ordered scene candidates
        """
        raw = str(self.script or "").strip()
        sentences = [
            part.strip(" \t\r\n-")
            for part in re.split(r"(?<=[\.\!\?؟])\s+|\n+", raw)
            if part and part.strip()
        ]
        return sentences

    def _split_visual_unit_into_clauses(self, unit: str) -> list[str]:
        """
        Splits one visual unit into smaller clauses when we need more scenes.

        Args:
            unit (str): current unit

        Returns:
            clauses (list[str]): smaller ordered clauses
        """
        parts = [
            part.strip(" \t\r\n-")
            for part in re.split(r"\s*[،,:;]\s*|\s+-\s+|\s+\bthen\b\s+|\s+\bwhile\b\s+|\s+\bbut\b\s+", unit, flags=re.IGNORECASE)
            if part and part.strip()
        ]
        return parts if len(parts) > 1 else [unit.strip()]

    def _split_script_into_spoken_beats(self) -> list[str]:
        """
        Splits the script into phrase-level spoken beats so scene changes can
        track the narration more closely than plain sentence-length weighting.

        Returns:
            beats (list[str]): ordered phrase-like beats
        """
        raw_units = self._split_script_into_sentences_for_visuals()
        if not raw_units:
            return []

        beats: list[str] = []
        splitter = re.compile(
            r"\s*[،,؛;:]\s*|"
            r"\s+\b(?:then|while|but|because|so|after|before|meanwhile)\b\s+|"
            r"\s+\b(?:وبعدين|بعدها|ثم|لكن|بس|عشان|علشان|وفي نفس الوقت|وبينما)\b\s+",
            flags=re.IGNORECASE,
        )

        for unit in raw_units:
            parts = [part.strip(" \t\r\n-") for part in splitter.split(unit) if part and part.strip()]
            if parts:
                beats.extend(parts)

        return beats or raw_units

    def _merge_units_evenly(self, units: list[str], target_count: int) -> list[str]:
        """
        Merges adjacent units into evenly distributed buckets so we preserve the
        full script even when we need fewer visual scenes than raw beats.

        Args:
            units (list[str]): ordered source units
            target_count (int): desired number of merged units

        Returns:
            merged (list[str]): merged ordered units
        """
        if target_count <= 0 or len(units) <= target_count:
            return [unit for unit in units if unit.strip()]

        total_units = len(units)
        merged = []
        for index in range(target_count):
            start = round(index * total_units / target_count)
            end = round((index + 1) * total_units / target_count)
            bucket = [unit.strip() for unit in units[start:end] if unit.strip()]
            if bucket:
                separator = "، " if any(self._contains_arabic_text(unit) for unit in bucket) else ", "
                merged.append(separator.join(bucket))

        return merged or [units[0]]

    def _build_scene_units(self) -> list[str]:
        """
        Builds a low-cost ordered scene plan directly from the script by using
        phrase-like beats first and then splitting longer units into clauses
        until we reach a reasonable number of visual beats.

        Returns:
            scene_units (list[str]): ordered scene units
        """
        units = self._split_script_into_spoken_beats()
        if not units:
            fallback = re.sub(r"\s+", " ", str(self.script or "").strip())
            return [fallback] if fallback else []

        min_prompts = max(1, get_min_image_prompts() or DEFAULT_MIN_IMAGE_PROMPTS)
        max_prompts = max(min_prompts, get_max_image_prompts() or len(units))
        target_count = max(min_prompts, min(max_prompts, len(units)))

        while len(units) < target_count:
            split_index = -1
            replacement = None
            best_length = 0

            for index, unit in enumerate(units):
                clauses = self._split_visual_unit_into_clauses(unit)
                if len(clauses) <= 1:
                    continue
                if len(unit) > best_length:
                    best_length = len(unit)
                    split_index = index
                    replacement = clauses

            if split_index == -1 or not replacement:
                break

            units = units[:split_index] + replacement + units[split_index + 1 :]

        units = [unit for unit in units if unit.strip()]
        if len(units) > target_count:
            units = self._merge_units_evenly(units, target_count)
        return units[:target_count]

    def _get_scene_units(self) -> list[str]:
        """
        Returns the current ordered scene units, creating them when needed.

        Returns:
            scene_units (list[str]): ordered scene units
        """
        if not getattr(self, "scene_units", None):
            self.scene_units = self._build_scene_units()
        return self.scene_units

    def _clean_image_prompt(self, prompt: str) -> str:
        """
        Removes numbering noise from LLM-generated prompts.

        Args:
            prompt (str): Raw prompt string

        Returns:
            cleaned (str): Cleaned prompt
        """
        cleaned = prompt.strip()
        cleaned = re.sub(r"^image prompt\s*\d+\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\d+[\).\:-]\s*", "", cleaned)
        return cleaned.strip()

    def _make_provider_friendly_image_prompt(self, prompt: str) -> str:
        """
        Rewrites a raw visual prompt into a shorter, safer prompt for image
        providers that often reject long scene descriptions or readable text.

        Args:
            prompt (str): raw prompt

        Returns:
            cleaned (str): provider-friendly prompt
        """
        cleaned = self._clean_image_prompt(prompt)
        cleaned = cleaned.replace('"', "").replace("'", "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")

        banned_phrases = (
            "readable text",
            "text on screen",
            "caption",
            "subtitle",
            "logo",
            "watermark",
            "UI overlay",
            "user interface",
            "sign with words",
            "poster text",
        )
        for phrase in banned_phrases:
            cleaned = re.sub(re.escape(phrase), "", cleaned, flags=re.IGNORECASE)

        words = cleaned.split()
        if len(words) > 24:
            cleaned = " ".join(words[:24]).strip()

        if not cleaned:
            cleaned = "cinematic vertical scene"

        locale_hint = self._get_visual_locale_hint()
        locale_prefix = f"{locale_hint}, " if locale_hint else ""

        return (
            f"Vertical cinematic still image, {locale_prefix}{cleaned}, "
            "photorealistic lighting, no readable text, no letters, no logos, no watermark."
        )

    def _image_prompts_need_translation(self, prompts: list[str]) -> bool:
        """
        Returns whether the generated prompts still contain Arabic script and
        should be normalized into concise English prompts for image providers.

        Args:
            prompts (list[str]): generated prompts

        Returns:
            needs_translation (bool): True when translation is recommended
        """
        arabic_pattern = re.compile(r"[\u0600-\u06FF]")
        return any(arabic_pattern.search(prompt or "") for prompt in prompts)

    def _translate_image_prompts_to_english(self, prompts: list[str]) -> list[str]:
        """
        Normalizes image prompts into concise English visual prompts in one
        cheaper LLM call when the original prompts contain Arabic script.

        Args:
            prompts (list[str]): raw generated prompts

        Returns:
            translated (list[str]): translated prompts, or the originals on failure
        """
        try:
            completion = (
                str(
                    self.generate_response(
                        "Translate the following JSON array of image prompts into concise English prompts for AI image generation. "
                        "Keep the same scene meaning, make them visual and cinematic, avoid readable text/logos/UI, "
                        "and return only a JSON array of strings.\n"
                        f"{self._get_visual_locale_prompt_block()}"
                        f"{json.dumps(prompts, ensure_ascii=False)}",
                        model_name=self._get_metadata_model_name(),
                    )
                )
                .replace("```json", "")
                .replace("```", "")
                .strip()
            )
            translated = json.loads(completion)
            if isinstance(translated, list):
                return [str(item).strip() for item in translated if str(item).strip()]
        except Exception:
            pass

        return prompts

    def _persist_image(self, image_bytes: bytes, provider_label: str, scene_index: int | None = None) -> str:
        """
        Writes generated image bytes to a PNG file in .mp.

        Args:
            image_bytes (bytes): Image payload
            provider_label (str): Label for logging
            scene_index (int | None): aligned scene index

        Returns:
            path (str): Absolute image path
        """
        image_index = len(self.images) + 1
        image_path = self._get_workspace_path(f"image_{image_index:02d}.png")

        with open(image_path, "wb") as image_file:
            image_file.write(image_bytes)

        if get_verbose():
            info(f' => Wrote image from {provider_label} to "{image_path}"')

        self.images.append(image_path)
        self.visual_assets.append({"type": "image", "path": image_path, "source": provider_label, "scene_index": scene_index})
        return image_path

    def _persist_binary_asset(self, asset_bytes: bytes, filename: str, source_label: str, asset_type: str, scene_index: int | None = None) -> str:
        """
        Writes a downloaded visual asset to the current workspace and tracks it
        for the final combine step.

        Args:
            asset_bytes (bytes): Downloaded file contents
            filename (str): Output filename
            source_label (str): Asset source/provider
            asset_type (str): "image" or "video"
            scene_index (int | None): aligned scene index

        Returns:
            path (str): Absolute asset path
        """
        asset_path = self._get_workspace_path(filename)
        with open(asset_path, "wb") as asset_file:
            asset_file.write(asset_bytes)

        if asset_type == "image":
            self.images.append(asset_path)

        self.visual_assets.append({"type": asset_type, "path": asset_path, "source": source_label, "scene_index": scene_index})
        if get_verbose():
            info(f' => Wrote {asset_type} from {source_label} to "{asset_path}"')
        return asset_path

    def _get_pixabay_cache_path(self) -> str:
        return os.path.join(ROOT_DIR, ".mp", "pixabay_cache.json")

    def _load_pixabay_cache(self) -> dict:
        cache_path = self._get_pixabay_cache_path()
        if not os.path.exists(cache_path):
            return {}

        try:
            with open(cache_path, "r", encoding="utf-8-sig") as file:
                return json.load(file) or {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_pixabay_cache(self, payload: dict) -> None:
        cache_path = self._get_pixabay_cache_path()
        with open(cache_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)

    def _search_pixabay(self, endpoint: str, params: dict) -> list[dict]:
        """
        Searches Pixabay with a simple 24h cache to respect API guidance.

        Args:
            endpoint (str): API endpoint path, e.g. "api" or "api/videos"
            params (dict): query params

        Returns:
            hits (list[dict]): result hits
        """
        api_key = get_pixabay_api_key()
        if not api_key:
            return []

        normalized_params = {key: params[key] for key in sorted(params)}
        cache_key = hashlib.sha256(
            json.dumps({"endpoint": endpoint, "params": normalized_params}, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        cache = self._load_pixabay_cache()
        cached_entry = cache.get(cache_key)
        now_ts = int(time.time())
        if cached_entry and now_ts - int(cached_entry.get("cached_at", 0)) < 86400:
            return cached_entry.get("hits", [])

        response = requests.get(
            f"https://pixabay.com/{endpoint}/",
            params={"key": api_key, **params},
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        hits = body.get("hits", [])
        cache[cache_key] = {"cached_at": now_ts, "hits": hits}
        self._save_pixabay_cache(cache)
        return hits

    def _derive_stock_queries(self) -> list[str]:
        """
        Builds simple Pixabay-friendly search queries from the current subject,
        tags, and generated prompts.

        Returns:
            queries (list[str]): stock search queries
        """
        try:
            prompt = f"""
            Generate a JSON array of short English stock-media search queries for Pixabay.
            Return exactly 5 search queries, each between 2 and 6 words.

            Subject: {self.subject}
            Script: {self.script}
            Tags: {", ".join(self.metadata.get("tags", []))}

            Rules:
            - English only
            - Search-friendly keywords, not full sentences
            - Focus on visual subjects, places, moods, or objects that could match this story
            - Return only a JSON array of strings
            """
            completion = (
                str(self.generate_response(prompt, model_name=self._get_metadata_model_name()))
                .replace("```json", "")
                .replace("```", "")
                .strip()
            )
            parsed = json.loads(completion)
            ai_queries = [
                re.sub(r"\s+", " ", str(item).strip())[:100]
                for item in parsed
                if str(item).strip()
            ]
            if ai_queries:
                return ai_queries[:5]
        except Exception:
            pass

        queries = []

        def add_query(value: str) -> None:
            cleaned = re.sub(r"\s+", " ", str(value or "").strip())
            cleaned = cleaned[:100].strip()
            if cleaned and cleaned not in queries:
                queries.append(cleaned)

        add_query(self.subject)
        for tag in self.metadata.get("tags", []):
            add_query(tag)
        for hashtag in self.metadata.get("hashtags", []):
            add_query(str(hashtag).replace("#", " "))
        for prompt in getattr(self, "image_prompts", [])[:6]:
            simplified = re.sub(r"[^A-Za-z0-9\s]", " ", prompt)
            simplified = " ".join(simplified.split()[:8])
            add_query(simplified)

        return queries[: max(3, len(getattr(self, "image_prompts", [])[:6]))]

    def _derive_stock_query_from_scene(self, scene_text: str, prompt_text: str) -> str:
        """
        Builds a short Pixabay-friendly query for one scene.

        Args:
            scene_text (str): script scene unit
            prompt_text (str): generated visual prompt for that scene

        Returns:
            query (str): short search query
        """
        preferred = re.sub(
            r"(?i)vertical cinematic still image|photorealistic lighting|no readable text|no letters|no logos|no watermark",
            "",
            str(prompt_text or ""),
        )
        preferred = re.sub(r"[^A-Za-z0-9\s]", " ", preferred)
        preferred = " ".join(preferred.split()[:6]).strip()
        if preferred:
            return preferred

        fallback = re.sub(r"\s+", " ", str(scene_text or "").strip())
        return fallback[:80].strip()

    def _clean_stock_search_query(self, value: str, max_words: int = 6) -> str:
        """
        Normalizes a raw scene description into a concise Pixabay-friendly query.

        Args:
            value (str): raw input text
            max_words (int): maximum retained words

        Returns:
            query (str): cleaned short query
        """
        cleaned = str(value or "")
        cleaned = re.sub(
            r"(?i)vertical cinematic still image|photorealistic lighting|no readable text|no letters|"
            r"no logos|no watermark|cinematic|photorealistic|vertical|still image|image prompt",
            " ",
            cleaned,
        )
        cleaned = cleaned.replace("&", " and ")
        cleaned = re.sub(r"[^A-Za-z0-9\s]", " ", cleaned)

        stopwords = {
            "the", "a", "an", "and", "or", "for", "with", "from", "into", "onto", "that", "this",
            "these", "those", "about", "over", "under", "through", "around", "inside", "outside",
            "what", "when", "where", "which", "who", "why", "how", "happens", "happen", "later",
            "does", "do", "did", "will", "would", "could", "should", "can", "after", "before",
            "scene", "shot", "camera", "background", "style", "visual", "cinematic", "image",
            "still", "prompt", "photo", "video", "footage", "screen", "showing", "shows",
            "there", "their", "them", "they", "your", "our", "his", "her", "its", "very",
        }

        words = []
        seen = set()
        for raw_word in cleaned.split():
            word = raw_word.strip().lower()
            if len(word) < 3 or word in stopwords or word.isdigit():
                continue
            if word not in seen:
                words.append(word)
                seen.add(word)
            if len(words) >= max_words:
                break

        return " ".join(words).strip()

    def _split_pixabay_tags(self, value: str) -> list[str]:
        """
        Splits Pixabay's comma-separated tag string into normalized phrases.

        Args:
            value (str): Pixabay tag string

        Returns:
            tags (list[str]): normalized tags
        """
        tags = []
        for item in re.split(r"\s*,\s*", str(value or "").strip()):
            cleaned = self._clean_stock_search_query(item, max_words=3)
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
        return tags

    def _get_stock_global_context(self) -> dict:
        """
        Builds broader topic context for scene-level Pixabay fallbacks.

        Returns:
            context (dict): summarized topic/search context
        """
        topic_terms = []

        def add_terms(value: str, max_words: int = 4) -> None:
            cleaned = self._clean_stock_search_query(value, max_words=max_words)
            if cleaned:
                for word in cleaned.split():
                    if word not in topic_terms:
                        topic_terms.append(word)

        add_terms(self.subject, max_words=5)
        for tag in self.metadata.get("tags", [])[:4]:
            add_terms(tag, max_words=3)
        for hashtag in self.metadata.get("hashtags", [])[:3]:
            add_terms(str(hashtag).replace("#", " "), max_words=3)

        return {
            "subject": self._clean_stock_search_query(self.subject, max_words=5),
            "topic_terms": topic_terms[:10],
            "topic_query": " ".join(topic_terms[:6]).strip(),
        }

    def _build_scene_query_variants(
        self,
        scene_text: str,
        prompt_text: str,
        scene_intent: dict,
        global_context: dict,
    ) -> list[dict]:
        """
        Builds prioritized search variants for one scene.

        Args:
            scene_text (str): scene narration text
            prompt_text (str): image prompt aligned to the scene
            scene_intent (dict): structured scene intent
            global_context (dict): broader video topic context

        Returns:
            variants (list[dict]): ordered search variants
        """
        variants = []

        def add_variant(variant_type: str, *parts: str) -> None:
            query = self._clean_stock_search_query(" ".join(str(part or "") for part in parts), max_words=6)
            if not query:
                return
            if any(existing["query"] == query for existing in variants):
                return
            variants.append({"type": variant_type, "query": query})

        add_variant("literal_scene", self._derive_stock_query_from_scene(scene_text, prompt_text))
        add_variant("subject_action", scene_intent.get("subject", ""), scene_intent.get("action", ""))
        add_variant("subject_setting", scene_intent.get("subject", ""), scene_intent.get("setting", "") or scene_intent.get("time", ""))
        if scene_intent.get("must_show"):
            add_variant("object_closeup", scene_intent["must_show"][0], scene_intent.get("setting", ""))
        add_variant("mood_context", scene_intent.get("mood", ""), scene_intent.get("setting", "") or scene_intent.get("subject", ""))
        add_variant("global_topic", global_context.get("topic_query", ""))
        add_variant("scene_fallback", scene_text)

        return variants[:5]

    def _build_fallback_scene_stock_intent(
        self,
        scene_index: int,
        scene_text: str,
        prompt_text: str,
        global_context: dict,
    ) -> dict:
        """
        Builds a structured scene intent without an LLM when needed.

        Args:
            scene_index (int): scene order
            scene_text (str): scene narration
            prompt_text (str): visual prompt for the scene
            global_context (dict): broader subject context

        Returns:
            intent (dict): normalized scene intent
        """
        prompt_terms = self._clean_stock_search_query(prompt_text, max_words=8).split()
        scene_terms = self._clean_stock_search_query(scene_text, max_words=8).split()
        combined_terms = prompt_terms or scene_terms or global_context.get("topic_terms", [])

        mood_words = {
            "calm", "tense", "panic", "dark", "hopeful", "happy", "sad", "dramatic",
            "luxury", "busy", "quiet", "mysterious", "urgent", "stress", "chaotic",
        }
        mood = next((word for word in combined_terms if word in mood_words), "")

        subject = " ".join(combined_terms[:3]).strip()
        action = " ".join(scene_terms[3:5] or prompt_terms[3:5]).strip()
        setting = " ".join(scene_terms[5:7] or global_context.get("topic_terms", [])[2:4]).strip()
        must_show = []
        for chunk in (subject, " ".join(combined_terms[2:5]).strip(), " ".join(combined_terms[4:7]).strip()):
            normalized = self._clean_stock_search_query(chunk, max_words=3)
            if normalized and normalized not in must_show:
                must_show.append(normalized)

        intent = {
            "scene_index": scene_index,
            "scene_text": re.sub(r"\s+", " ", str(scene_text or "").strip()),
            "prompt_text": re.sub(r"\s+", " ", str(prompt_text or "").strip()),
            "subject": self._clean_stock_search_query(subject, max_words=4),
            "action": self._clean_stock_search_query(action, max_words=3),
            "setting": self._clean_stock_search_query(setting, max_words=3),
            "time": "",
            "mood": self._clean_stock_search_query(mood, max_words=2),
            "must_show": must_show[:3],
            "must_avoid": [],
        }
        intent["query_variants"] = self._build_scene_query_variants(
            scene_text,
            prompt_text,
            intent,
            global_context,
        )
        return intent

    def _normalize_scene_stock_intent(
        self,
        raw_intent: dict | None,
        scene_index: int,
        scene_text: str,
        prompt_text: str,
        global_context: dict,
    ) -> dict:
        """
        Normalizes one raw scene-intent object into a predictable structure.

        Args:
            raw_intent (dict | None): raw LLM payload
            scene_index (int): scene order
            scene_text (str): script scene text
            prompt_text (str): aligned image prompt
            global_context (dict): broader stock-search context

        Returns:
            intent (dict): normalized scene intent
        """
        fallback = self._build_fallback_scene_stock_intent(
            scene_index,
            scene_text,
            prompt_text,
            global_context,
        )
        if not isinstance(raw_intent, dict):
            return fallback

        intent = {
            "scene_index": scene_index,
            "scene_text": re.sub(r"\s+", " ", str(scene_text or "").strip()),
            "prompt_text": re.sub(r"\s+", " ", str(prompt_text or "").strip()),
            "subject": self._clean_stock_search_query(raw_intent.get("subject", ""), max_words=4) or fallback["subject"],
            "action": self._clean_stock_search_query(raw_intent.get("action", ""), max_words=3) or fallback["action"],
            "setting": self._clean_stock_search_query(raw_intent.get("setting", ""), max_words=3) or fallback["setting"],
            "time": self._clean_stock_search_query(raw_intent.get("time", ""), max_words=3),
            "mood": self._clean_stock_search_query(raw_intent.get("mood", ""), max_words=2) or fallback["mood"],
            "must_show": [],
            "must_avoid": [],
        }

        raw_must_show = raw_intent.get("must_show", [])
        if not isinstance(raw_must_show, list):
            raw_must_show = [raw_must_show]
        for item in raw_must_show:
            cleaned = self._clean_stock_search_query(item, max_words=3)
            if cleaned and cleaned not in intent["must_show"]:
                intent["must_show"].append(cleaned)
        if not intent["must_show"]:
            intent["must_show"] = fallback["must_show"][:]

        raw_must_avoid = raw_intent.get("must_avoid", [])
        if not isinstance(raw_must_avoid, list):
            raw_must_avoid = [raw_must_avoid]
        for item in raw_must_avoid:
            cleaned = self._clean_stock_search_query(item, max_words=3)
            if cleaned and cleaned not in intent["must_avoid"]:
                intent["must_avoid"].append(cleaned)

        query_variants = []

        def add_query_variant(variant_type: str, query: str) -> None:
            cleaned = self._clean_stock_search_query(query, max_words=6)
            if not cleaned:
                return
            if any(existing["query"] == cleaned for existing in query_variants):
                return
            query_variants.append({"type": variant_type, "query": cleaned})

        raw_variants = raw_intent.get("query_variants", [])
        if isinstance(raw_variants, list):
            for item in raw_variants:
                if isinstance(item, dict):
                    add_query_variant(str(item.get("type", "llm_variant")).strip() or "llm_variant", item.get("query", ""))
                else:
                    add_query_variant("llm_variant", str(item))

        for variant in self._build_scene_query_variants(scene_text, prompt_text, intent, global_context):
            add_query_variant(variant["type"], variant["query"])

        intent["query_variants"] = query_variants[:5]
        return intent

    def _build_scene_stock_intents(self, scene_units: list[str], prompt_texts: list[str]) -> tuple[list[dict], dict]:
        """
        Builds structured visual intents for all scenes in one cheaper LLM call,
        with heuristic fallbacks if parsing fails.

        Args:
            scene_units (list[str]): aligned script scenes
            prompt_texts (list[str]): aligned prompts

        Returns:
            intents_and_context (tuple[list[dict], dict]): normalized scene intents and global context
        """
        global_context = self._get_stock_global_context()
        fallback_intents = [
            self._build_fallback_scene_stock_intent(
                scene_index=index,
                scene_text=scene_units[index],
                prompt_text=prompt_texts[index] if index < len(prompt_texts) else "",
                global_context=global_context,
            )
            for index in range(len(scene_units))
        ]

        if not scene_units:
            return fallback_intents, global_context

        try:
            scene_payload = [
                {
                    "scene_index": index,
                    "scene_text": scene_units[index],
                    "prompt_text": prompt_texts[index] if index < len(prompt_texts) else "",
                }
                for index in range(len(scene_units))
            ]
            completion = (
                str(
                    self.generate_response(
                        "Return only JSON.\n"
                        f"Build Pixabay stock-search scene intents for exactly {len(scene_units)} scenes.\n"
                        "For each scene return an object with these keys:\n"
                        "subject, action, setting, time, mood as short English strings.\n"
                        "must_show and must_avoid as arrays of short English phrases.\n"
                        "query_variants as an array of up to 5 objects with keys type and query.\n"
                        "Rules:\n"
                        "- English only.\n"
                        "- Keep each query between 2 and 6 words.\n"
                        "- Prefer concrete visual search terms for stock footage.\n"
                        "- Avoid brand names, captions, logos, UI terms, and abstract essay language.\n"
                        "- Query types should prioritize literal_scene, subject_action, subject_setting, object_closeup, mood_context, global_topic.\n"
                        f"Global subject: {self.subject}\n"
                        f"Tags: {', '.join(self.metadata.get('tags', []))}\n"
                        f"Hashtags: {', '.join(self.metadata.get('hashtags', []))}\n"
                        f"Scenes JSON: {json.dumps(scene_payload, ensure_ascii=False)}",
                        model_name=self._get_metadata_model_name(),
                    )
                )
                .replace("```json", "")
                .replace("```", "")
                .strip()
            )
            parsed = json.loads(completion)
            if isinstance(parsed, list) and len(parsed) == len(scene_units):
                intents = [
                    self._normalize_scene_stock_intent(
                        parsed[index],
                        scene_index=index,
                        scene_text=scene_units[index],
                        prompt_text=prompt_texts[index] if index < len(prompt_texts) else "",
                        global_context=global_context,
                    )
                    for index in range(len(scene_units))
                ]
                return intents, global_context
        except Exception:
            pass

        return fallback_intents, global_context

    def _tokenize_search_text(self, value: str) -> set[str]:
        """
        Turns a free-text search phrase into normalized keyword tokens.

        Args:
            value (str): source text

        Returns:
            tokens (set[str]): normalized tokens
        """
        stopwords = {
            "the", "a", "an", "and", "or", "with", "what", "if", "into", "from", "after",
            "before", "over", "under", "city", "people", "person", "scene", "vertical",
            "cinematic", "image", "still", "lighting", "photorealistic", "setting",
        }
        words = re.findall(r"[A-Za-z0-9]+", str(value or "").lower())
        return {word for word in words if len(word) > 2 and word not in stopwords}

    def _get_pixabay_image_url(self, hit: dict) -> str | None:
        """
        Returns the preferred image URL for a Pixabay image hit.

        Args:
            hit (dict): Pixabay image result

        Returns:
            url (str | None): best downloadable URL
        """
        return hit.get("largeImageURL") or hit.get("webformatURL")

    def _score_pixabay_query_variant_priority(self, variant_type: str) -> float:
        """
        Returns a preference bonus based on how semantically close a query
        variant is to the scene.

        Args:
            variant_type (str): query variant label

        Returns:
            bonus (float): additive score bonus
        """
        priorities = {
            "literal_scene": 8.0,
            "subject_action": 7.0,
            "subject_setting": 6.0,
            "object_closeup": 5.5,
            "mood_context": 4.5,
            "global_topic": 3.0,
            "scene_fallback": 2.5,
            "llm_variant": 5.0,
        }
        return priorities.get(str(variant_type or "").strip().lower(), 3.5)

    def _estimate_pixabay_generic_penalty(self, tag_tokens: set[str], relevance_tokens: set[str]) -> float:
        """
        Penalizes candidates whose tags are generic and weakly tied to the scene.

        Args:
            tag_tokens (set[str]): normalized Pixabay tag tokens
            relevance_tokens (set[str]): normalized scene relevance tokens

        Returns:
            penalty (float): genericity penalty
        """
        generic_tokens = {
            "nature", "beautiful", "beauty", "travel", "outdoor", "outside", "landscape",
            "background", "summer", "sunset", "sunrise", "people", "person", "lifestyle",
            "abstract", "business", "city", "light", "color", "motion", "water", "sky",
        }
        generic_overlap = len(tag_tokens & generic_tokens)
        relevant_overlap = len(tag_tokens & relevance_tokens)
        penalty = 0.0
        if relevant_overlap == 0:
            penalty += 4.0
        if generic_overlap >= 2:
            penalty += generic_overlap * 1.5
        if len(tag_tokens) <= 2:
            penalty += 1.5
        return penalty

    def _build_pixabay_candidate(
        self,
        hit: dict,
        asset_type: str,
        query_variant: dict,
        scene_intent: dict,
        preferred_duration: float | None = None,
    ) -> dict | None:
        """
        Builds one normalized scored Pixabay candidate for a scene.

        Args:
            hit (dict): Pixabay result hit
            asset_type (str): video or image
            query_variant (dict): active query variant
            scene_intent (dict): structured scene intent
            preferred_duration (float | None): scene duration target

        Returns:
            candidate (dict | None): scored candidate or None if invalid
        """
        query = str(query_variant.get("query", "")).strip()
        if not query:
            return None

        if asset_type == "video":
            if not self._is_pixabay_video_duration_usable(hit, preferred_duration):
                return None
            asset_url, asset_variant = self._choose_pixabay_video_url(hit)
            if not asset_url:
                return None
            baseline_score = self._score_pixabay_video_hit(hit, query, preferred_duration)
        else:
            asset_url = self._get_pixabay_image_url(hit)
            asset_variant = ""
            if not asset_url:
                return None
            baseline_score = self._score_pixabay_image_hit(hit, query)

        tag_tokens = self._tokenize_search_text(hit.get("tags", ""))
        query_tokens = self._tokenize_search_text(query)
        subject_tokens = self._tokenize_search_text(scene_intent.get("subject", ""))
        action_tokens = self._tokenize_search_text(scene_intent.get("action", ""))
        setting_tokens = self._tokenize_search_text(scene_intent.get("setting", ""))
        time_tokens = self._tokenize_search_text(scene_intent.get("time", ""))
        mood_tokens = self._tokenize_search_text(scene_intent.get("mood", ""))
        must_show_tokens = set()
        for item in scene_intent.get("must_show", []):
            must_show_tokens.update(self._tokenize_search_text(item))
        must_avoid_tokens = set()
        for item in scene_intent.get("must_avoid", []):
            must_avoid_tokens.update(self._tokenize_search_text(item))

        query_overlap = len(query_tokens & tag_tokens)
        subject_match = len(subject_tokens & tag_tokens)
        action_match = len(action_tokens & tag_tokens)
        setting_match = len(setting_tokens & tag_tokens)
        time_match = len(time_tokens & tag_tokens)
        mood_match = len(mood_tokens & tag_tokens)
        must_show_match = len(must_show_tokens & tag_tokens)
        must_avoid_overlap = len(must_avoid_tokens & tag_tokens)

        relevance_tokens = query_tokens | subject_tokens | action_tokens | setting_tokens | time_tokens | mood_tokens | must_show_tokens
        variant_bonus = self._score_pixabay_query_variant_priority(query_variant.get("type", ""))
        generic_penalty = self._estimate_pixabay_generic_penalty(tag_tokens, relevance_tokens)
        asset_type_bonus = 3.5 if asset_type == "video" else 1.5
        structured_match_score = (
            (subject_match * 8.5)
            + (action_match * 6.0)
            + (setting_match * 5.5)
            + (time_match * 3.0)
            + (mood_match * 2.5)
            + (must_show_match * 6.5)
            + (query_overlap * 3.0)
        )
        avoid_penalty = must_avoid_overlap * 8.0
        score = baseline_score + variant_bonus + asset_type_bonus + structured_match_score - generic_penalty - avoid_penalty

        return {
            "candidate_key": f"{asset_type}:{hit.get('id') or asset_url}",
            "asset_type": asset_type,
            "asset_url": asset_url,
            "asset_variant": asset_variant,
            "hit_id": hit.get("id"),
            "user": str(hit.get("user", "")).strip().lower(),
            "user_display": str(hit.get("user", "")).strip(),
            "top_tags": self._split_pixabay_tags(hit.get("tags", ""))[:6],
            "tag_tokens": tag_tokens,
            "best_query": query,
            "best_query_type": str(query_variant.get("type", "")).strip() or "variant",
            "matched_queries": [query],
            "matched_query_types": [str(query_variant.get("type", "")).strip() or "variant"],
            "base_score": float(score),
            "score_breakdown": {
                "baseline": round(float(baseline_score), 3),
                "variant_bonus": round(float(variant_bonus), 3),
                "asset_type_bonus": round(float(asset_type_bonus), 3),
                "subject_match": subject_match,
                "action_match": action_match,
                "setting_match": setting_match,
                "time_match": time_match,
                "mood_match": mood_match,
                "must_show_match": must_show_match,
                "must_avoid_overlap": must_avoid_overlap,
                "query_overlap": query_overlap,
                "generic_penalty": round(float(generic_penalty), 3),
                "avoid_penalty": round(float(avoid_penalty), 3),
            },
            "duration": float(hit.get("duration") or 0.0),
            "views": int(hit.get("views", 0) or 0),
            "likes": int(hit.get("likes", 0) or 0),
            "raw_hit": hit,
        }

    def _collect_pixabay_scene_candidates(
        self,
        scene_intent: dict,
        preferred_duration: float | None = None,
    ) -> list[dict]:
        """
        Searches Pixabay using all query variants for one scene, merges the
        results, and keeps the strongest deduplicated candidate set.

        Args:
            scene_intent (dict): normalized scene intent
            preferred_duration (float | None): scene duration target

        Returns:
            candidates (list[dict]): ranked merged candidate pool
        """
        candidates_by_key: dict[str, dict] = {}
        per_page = max(4, get_pixabay_results_per_query())

        for query_variant in scene_intent.get("query_variants", []):
            query = str(query_variant.get("query", "")).strip()
            if not query:
                continue

            params = {
                "q": query,
                "safesearch": "true",
                "per_page": per_page,
            }

            try:
                for hit in self._search_pixabay("api/videos", params):
                    candidate = self._build_pixabay_candidate(
                        hit,
                        asset_type="video",
                        query_variant=query_variant,
                        scene_intent=scene_intent,
                        preferred_duration=preferred_duration,
                    )
                    if candidate is None:
                        continue
                    existing = candidates_by_key.get(candidate["candidate_key"])
                    if existing is None:
                        candidates_by_key[candidate["candidate_key"]] = candidate
                    else:
                        if candidate["best_query"] not in existing["matched_queries"]:
                            existing["matched_queries"].append(candidate["best_query"])
                        if candidate["best_query_type"] not in existing["matched_query_types"]:
                            existing["matched_query_types"].append(candidate["best_query_type"])
                        if candidate["base_score"] > existing["base_score"]:
                            existing.update(
                                {
                                    "best_query": candidate["best_query"],
                                    "best_query_type": candidate["best_query_type"],
                                    "base_score": candidate["base_score"],
                                    "score_breakdown": candidate["score_breakdown"],
                                    "duration": candidate["duration"],
                                    "views": candidate["views"],
                                    "likes": candidate["likes"],
                                }
                            )
            except Exception as exc:
                if get_verbose():
                    warning(f'Pixabay video lookup failed for "{query}": {exc}')

            try:
                for hit in self._search_pixabay(
                    "api",
                    {
                        **params,
                        "image_type": "photo",
                        "orientation": "vertical",
                    },
                ):
                    candidate = self._build_pixabay_candidate(
                        hit,
                        asset_type="image",
                        query_variant=query_variant,
                        scene_intent=scene_intent,
                        preferred_duration=preferred_duration,
                    )
                    if candidate is None:
                        continue
                    existing = candidates_by_key.get(candidate["candidate_key"])
                    if existing is None:
                        candidates_by_key[candidate["candidate_key"]] = candidate
                    else:
                        if candidate["best_query"] not in existing["matched_queries"]:
                            existing["matched_queries"].append(candidate["best_query"])
                        if candidate["best_query_type"] not in existing["matched_query_types"]:
                            existing["matched_query_types"].append(candidate["best_query_type"])
                        if candidate["base_score"] > existing["base_score"]:
                            existing.update(
                                {
                                    "best_query": candidate["best_query"],
                                    "best_query_type": candidate["best_query_type"],
                                    "base_score": candidate["base_score"],
                                    "score_breakdown": candidate["score_breakdown"],
                                    "views": candidate["views"],
                                    "likes": candidate["likes"],
                                }
                            )
            except Exception as exc:
                if get_verbose():
                    warning(f'Pixabay image lookup failed for "{query}": {exc}')

        return sorted(
            candidates_by_key.values(),
            key=lambda item: (
                float(item.get("base_score", 0.0)),
                len(item.get("matched_queries", [])),
                int(item.get("views", 0)),
                int(item.get("likes", 0)),
            ),
            reverse=True,
        )

    def _calculate_pixabay_diversity_penalty(self, candidate: dict, selected_candidates: list[dict]) -> tuple[float, dict]:
        """
        Penalizes near-duplicate Pixabay choices across scenes.

        Args:
            candidate (dict): candidate under consideration
            selected_candidates (list[dict]): already selected scene candidates

        Returns:
            penalty_and_breakdown (tuple[float, dict]): diversity penalty and explanation
        """
        same_uploader_count = 0
        shared_tag_penalty = 0.0
        dominant_tag_penalty = 0.0
        repeated_query_penalty = 0.0

        candidate_tags = set(candidate.get("tag_tokens", set()))
        candidate_dominant_tags = candidate.get("top_tags", [])[:3]

        for selected in selected_candidates:
            if candidate.get("user") and candidate.get("user") == selected.get("user"):
                same_uploader_count += 1

            shared_tags = len(candidate_tags & set(selected.get("tag_tokens", set())))
            if shared_tags >= 2:
                shared_tag_penalty += min(9.0, shared_tags * 2.2)

            selected_dominant_tags = selected.get("top_tags", [])[:3]
            dominant_tag_penalty += sum(
                2.5
                for tag in candidate_dominant_tags
                if tag and tag in selected_dominant_tags
            )

            if candidate.get("best_query") and candidate.get("best_query") == selected.get("best_query"):
                repeated_query_penalty += 1.5

        uploader_penalty = same_uploader_count * 6.0
        total_penalty = uploader_penalty + shared_tag_penalty + dominant_tag_penalty + repeated_query_penalty
        return total_penalty, {
            "same_uploader_count": same_uploader_count,
            "uploader_penalty": round(float(uploader_penalty), 3),
            "shared_tag_penalty": round(float(shared_tag_penalty), 3),
            "dominant_tag_penalty": round(float(dominant_tag_penalty), 3),
            "repeated_query_penalty": round(float(repeated_query_penalty), 3),
        }

    def _select_pixabay_candidates_across_scenes(self, scene_plans: list[dict]) -> dict[int, dict]:
        """
        Selects one Pixabay candidate per scene after all candidate pools are
        known, so we can penalize repeated visual patterns across the run.

        Args:
            scene_plans (list[dict]): scene plans with candidate pools

        Returns:
            selected (dict[int, dict]): selected candidate per scene index
        """
        selected: dict[int, dict] = {}
        selected_candidates: list[dict] = []
        used_candidate_keys: set[str] = set()

        scene_order = sorted(
            range(len(scene_plans)),
            key=lambda index: (
                len(scene_plans[index].get("candidates", [])),
                -float(scene_plans[index].get("candidates", [{}])[0].get("base_score", -9999.0))
                if scene_plans[index].get("candidates")
                else 0.0,
                index,
            ),
        )

        for scene_index in scene_order:
            plan = scene_plans[scene_index]
            best_choice = None

            for candidate in plan.get("candidates", []):
                if candidate["candidate_key"] in used_candidate_keys:
                    continue

                diversity_penalty, diversity_breakdown = self._calculate_pixabay_diversity_penalty(
                    candidate,
                    selected_candidates,
                )
                final_score = float(candidate.get("base_score", 0.0)) - diversity_penalty
                if best_choice is None or final_score > best_choice["final_score"]:
                    best_choice = {
                        **candidate,
                        "final_score": final_score,
                        "diversity_penalty": diversity_penalty,
                        "diversity_breakdown": diversity_breakdown,
                    }

            if best_choice is None:
                continue

            selected[scene_index] = best_choice
            used_candidate_keys.add(best_choice["candidate_key"])
            selected_candidates.append(best_choice)

        return selected

    def _summarize_pixabay_candidate_for_debug(self, candidate: dict, selected: bool = False) -> dict:
        """
        Converts an internal Pixabay candidate into a JSON-safe debug summary.

        Args:
            candidate (dict): candidate payload
            selected (bool): whether this candidate won the scene

        Returns:
            summary (dict): JSON-safe summary
        """
        return {
            "selected": selected,
            "asset_type": candidate.get("asset_type"),
            "asset_id": candidate.get("hit_id"),
            "asset_url": candidate.get("asset_url"),
            "asset_variant": candidate.get("asset_variant"),
            "user": candidate.get("user_display"),
            "tags": candidate.get("top_tags", []),
            "best_query": candidate.get("best_query"),
            "best_query_type": candidate.get("best_query_type"),
            "matched_queries": candidate.get("matched_queries", [])[:5],
            "matched_query_types": candidate.get("matched_query_types", [])[:5],
            "base_score": round(float(candidate.get("base_score", 0.0)), 3),
            "final_score": round(float(candidate.get("final_score", candidate.get("base_score", 0.0))), 3),
            "diversity_penalty": round(float(candidate.get("diversity_penalty", 0.0)), 3),
            "score_breakdown": candidate.get("score_breakdown", {}),
            "diversity_breakdown": candidate.get("diversity_breakdown", {}),
            "duration": round(float(candidate.get("duration", 0.0)), 3),
            "views": int(candidate.get("views", 0) or 0),
            "likes": int(candidate.get("likes", 0) or 0),
        }

    def _write_pixabay_debug_report(self, payload: dict) -> None:
        """
        Writes a detailed Pixabay selection report into the run workspace.

        Args:
            payload (dict): debug payload

        Returns:
            None
        """
        self._pixabay_selection_debug = payload.get("scenes", [])
        debug_path = self._get_workspace_path("pixabay_selection_debug.json")
        with open(debug_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)

    def _plan_pixabay_scene_assets(
        self,
        scene_units: list[str],
        prompt_texts: list[str],
        preferred_scene_durations: list[float],
    ) -> dict[int, dict]:
        """
        Plans, selects, downloads, and tracks the best Pixabay assets for all
        scenes in one coordinated pass.

        Args:
            scene_units (list[str]): ordered scene texts
            prompt_texts (list[str]): aligned image prompts
            preferred_scene_durations (list[float]): estimated scene durations

        Returns:
            assets_by_scene (dict[int, dict]): downloaded asset descriptors by scene
        """
        assets_by_scene: dict[int, dict] = {}
        scene_intents, global_context = self._build_scene_stock_intents(scene_units, prompt_texts)
        scene_plans = []

        for scene_index, scene_text in enumerate(scene_units):
            prompt_text = prompt_texts[scene_index] if scene_index < len(prompt_texts) else ""
            scene_intent = scene_intents[scene_index] if scene_index < len(scene_intents) else self._build_fallback_scene_stock_intent(
                scene_index,
                scene_text,
                prompt_text,
                global_context,
            )
            preferred_duration = preferred_scene_durations[min(scene_index, len(preferred_scene_durations) - 1)] if preferred_scene_durations else None
            candidates = self._collect_pixabay_scene_candidates(scene_intent, preferred_duration=preferred_duration)
            scene_plans.append(
                {
                    "scene_index": scene_index,
                    "scene_text": scene_text,
                    "prompt_text": prompt_text,
                    "preferred_duration": preferred_duration,
                    "scene_intent": scene_intent,
                    "candidates": candidates,
                }
            )

        selected_by_scene = self._select_pixabay_candidates_across_scenes(scene_plans)
        debug_scenes = []

        for plan in scene_plans:
            scene_index = plan["scene_index"]
            selected_candidate = selected_by_scene.get(scene_index)
            selected_summary = None
            local_asset_path = None

            if selected_candidate is not None:
                try:
                    asset_bytes = self._download_url_bytes(selected_candidate["asset_url"])
                    if asset_bytes:
                        if selected_candidate["asset_type"] == "video":
                            filename = (
                                f"stock_video_{len(self.visual_assets)+1:02d}_"
                                f"{selected_candidate.get('hit_id') or 'pixabay'}_"
                                f"{selected_candidate.get('asset_variant') or 'clip'}.mp4"
                            )
                        else:
                            filename = f"stock_image_{len(self.visual_assets)+1:02d}_{selected_candidate.get('hit_id') or 'pixabay'}.jpg"

                        path = self._persist_binary_asset(
                            asset_bytes,
                            filename,
                            "Pixabay",
                            selected_candidate["asset_type"],
                            scene_index=scene_index,
                        )
                        self._record_stock_asset_cost("pixabay", selected_candidate["asset_type"], scene_index=scene_index)
                        local_asset_path = path
                        if self.visual_assets and self.visual_assets[-1].get("path") == path:
                            self.visual_assets[-1].update(
                                {
                                    "pixabay_hit_id": selected_candidate.get("hit_id"),
                                    "pixabay_query": selected_candidate.get("best_query"),
                                    "pixabay_query_type": selected_candidate.get("best_query_type"),
                                    "pixabay_tags": selected_candidate.get("top_tags", []),
                                }
                            )
                            assets_by_scene[scene_index] = self.visual_assets[-1]
                        else:
                            assets_by_scene[scene_index] = {
                                "type": selected_candidate["asset_type"],
                                "path": path,
                                "source": "Pixabay",
                                "scene_index": scene_index,
                            }
                        selected_summary = self._summarize_pixabay_candidate_for_debug(selected_candidate, selected=True)
                except Exception as exc:
                    if get_verbose():
                        warning(f'Failed to download selected Pixabay asset for scene {scene_index + 1}: {exc}')

            top_candidates = []
            for candidate in plan.get("candidates", [])[:5]:
                is_selected = bool(selected_candidate and candidate["candidate_key"] == selected_candidate["candidate_key"])
                candidate_for_debug = selected_candidate if is_selected else candidate
                top_candidates.append(self._summarize_pixabay_candidate_for_debug(candidate_for_debug, selected=is_selected))

            debug_scenes.append(
                {
                    "scene_index": scene_index,
                    "scene_text": plan["scene_text"],
                    "prompt_text": plan["prompt_text"],
                    "preferred_duration": round(float(plan["preferred_duration"] or 0.0), 3),
                    "intent": {
                        "subject": plan["scene_intent"].get("subject", ""),
                        "action": plan["scene_intent"].get("action", ""),
                        "setting": plan["scene_intent"].get("setting", ""),
                        "time": plan["scene_intent"].get("time", ""),
                        "mood": plan["scene_intent"].get("mood", ""),
                        "must_show": plan["scene_intent"].get("must_show", []),
                        "must_avoid": plan["scene_intent"].get("must_avoid", []),
                    },
                    "query_variants": plan["scene_intent"].get("query_variants", []),
                    "candidate_count": len(plan.get("candidates", [])),
                    "selected_asset": selected_summary,
                    "local_asset_path": local_asset_path,
                    "top_candidates": top_candidates,
                }
            )

        self._write_pixabay_debug_report(
            {
                "subject": self.subject,
                "script": self.script,
                "global_context": global_context,
                "scenes": debug_scenes,
            }
        )
        return assets_by_scene

    def _estimate_scene_durations_for_asset_search(self) -> list[float]:
        """
        Estimates scene durations before TTS is rendered so we can prefer
        stock videos whose native duration is close to the intended scene.

        Returns:
            durations (list[float]): estimated scene durations
        """
        target_total_duration = max(5, get_youtube_target_duration_seconds())
        return self._get_scene_durations(float(target_total_duration))

    def _score_pixabay_video_hit(self, hit: dict, query: str, preferred_duration: float | None = None) -> float:
        """
        Scores a Pixabay video hit for suitability.

        Args:
            hit (dict): Pixabay hit
            query (str): scene query
            preferred_duration (float | None): desired scene duration

        Returns:
            score (float): higher is better
        """
        query_tokens = self._tokenize_search_text(query)
        tag_tokens = self._tokenize_search_text(hit.get("tags", ""))
        overlap = len(query_tokens & tag_tokens)

        duration = float(hit.get("duration") or 0)
        duration_score = 0.0
        if preferred_duration and duration > 0:
            duration_score = max(0.0, 8.0 - abs(duration - preferred_duration))
            if duration > max(18.0, preferred_duration * 3.5):
                duration_score -= 10.0

        views_score = min(8.0, float(hit.get("views", 0)) / 150000.0)
        likes_score = min(6.0, float(hit.get("likes", 0)) / 400.0)
        quality_bonus = 3.0 if not hit.get("isLowQuality", False) else -4.0
        ai_penalty = -3.0 if hit.get("isAiGenerated", False) else 0.0
        film_bonus = 2.0 if str(hit.get("type", "")).lower() == "film" else 0.0

        return overlap * 10.0 + duration_score + views_score + likes_score + quality_bonus + ai_penalty + film_bonus

    def _is_pixabay_video_duration_usable(self, hit: dict, preferred_duration: float | None) -> bool:
        """
        Returns whether a Pixabay video duration is reasonable for the target
        scene duration.

        Args:
            hit (dict): Pixabay video hit
            preferred_duration (float | None): desired scene duration

        Returns:
            usable (bool): True if the duration is acceptable
        """
        duration = float(hit.get("duration") or 0)
        if duration <= 0:
            return False
        if preferred_duration is None:
            return duration <= 20.0
        return duration <= max(18.0, preferred_duration * 3.5)

    def _score_pixabay_image_hit(self, hit: dict, query: str) -> float:
        """
        Scores a Pixabay image hit for suitability.

        Args:
            hit (dict): Pixabay hit
            query (str): scene query

        Returns:
            score (float): higher is better
        """
        query_tokens = self._tokenize_search_text(query)
        tag_tokens = self._tokenize_search_text(hit.get("tags", ""))
        overlap = len(query_tokens & tag_tokens)

        width = float(hit.get("imageWidth") or 0)
        height = float(hit.get("imageHeight") or 0)
        orientation_score = 0.0
        if width > 0 and height > 0:
            ratio = width / height
            orientation_score = max(0.0, 4.0 - abs(ratio - 0.5625) * 6.0)

        views_score = min(6.0, float(hit.get("views", 0)) / 200000.0)
        likes_score = min(5.0, float(hit.get("likes", 0)) / 250.0)
        quality_bonus = 2.0 if not hit.get("isLowQuality", False) else -3.0
        ai_penalty = -2.0 if hit.get("isAiGenerated", False) else 0.0

        return overlap * 10.0 + orientation_score + views_score + likes_score + quality_bonus + ai_penalty

    def _download_url_bytes(self, url: str) -> bytes | None:
        if not url:
            return None
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        return response.content

    def _choose_pixabay_video_url(self, hit: dict) -> tuple[str | None, str]:
        videos = hit.get("videos", {}) or {}
        for key in ("medium", "large", "small", "tiny"):
            item = videos.get(key) or {}
            url = item.get("url")
            if url:
                return url, key
        return None, ""

    def _fetch_pixabay_stock_asset(
        self,
        query: str,
        used_urls: set[str],
        scene_index: int | None = None,
        preferred_duration: float | None = None,
    ) -> dict | None:
        """
        Fetches one Pixabay stock asset, preferring video then falling back to image.

        Args:
            query (str): search query
            used_urls (set[str]): URLs already used in this run
            scene_index (int | None): aligned scene index
            preferred_duration (float | None): target scene duration

        Returns:
            asset (dict | None): asset descriptor
        """
        params = {
            "q": query,
            "safesearch": "true",
            "editors_choice": "true",
            "per_page": get_pixabay_results_per_query(),
        }

        try:
            video_hits = self._search_pixabay("api/videos", params)
            ranked_video_hits = sorted(
                video_hits,
                key=lambda hit: self._score_pixabay_video_hit(hit, query, preferred_duration),
                reverse=True,
            )
            for hit in ranked_video_hits:
                if not self._is_pixabay_video_duration_usable(hit, preferred_duration):
                    continue
                video_url, variant = self._choose_pixabay_video_url(hit)
                if not video_url or video_url in used_urls:
                    continue
                used_urls.add(video_url)
                video_bytes = self._download_url_bytes(video_url)
                if not video_bytes:
                    continue
                filename = f"stock_video_{len(self.visual_assets)+1:02d}_{variant}.mp4"
                path = self._persist_binary_asset(video_bytes, filename, "Pixabay", "video", scene_index=scene_index)
                self._record_stock_asset_cost("pixabay", "video", scene_index=scene_index)
                return {"type": "video", "path": path}
        except Exception as exc:
            if get_verbose():
                warning(f'Pixabay video lookup failed for "{query}": {exc}')

        try:
            image_hits = self._search_pixabay(
                "api",
                {
                    **params,
                    "image_type": "photo",
                    "orientation": "vertical",
                },
            )
            ranked_image_hits = sorted(
                image_hits,
                key=lambda hit: self._score_pixabay_image_hit(hit, query),
                reverse=True,
            )
            for hit in ranked_image_hits:
                image_url = hit.get("largeImageURL") or hit.get("webformatURL")
                if not image_url or image_url in used_urls:
                    continue
                used_urls.add(image_url)
                image_bytes = self._download_url_bytes(image_url)
                if not image_bytes:
                    continue
                filename = f"stock_image_{len(self.visual_assets)+1:02d}.jpg"
                path = self._persist_binary_asset(image_bytes, filename, "Pixabay", "image", scene_index=scene_index)
                self._record_stock_asset_cost("pixabay", "image", scene_index=scene_index)
                return {"type": "image", "path": path}
        except Exception as exc:
            if get_verbose():
                warning(f'Pixabay image lookup failed for "{query}": {exc}')

        return None

    def _generate_visual_assets(self) -> int:
        """
        Creates visual assets based on the configured sourcing strategy.

        Returns:
            count (int): total generated/downloaded assets
        """
        strategy = get_asset_strategy()
        scene_units = self._get_scene_units()
        target_count = len(scene_units)
        ai_used = 0
        estimated_scene_durations = self._estimate_scene_durations_for_asset_search()
        self._pixabay_selection_debug = []
        pixabay_assets_by_scene: dict[int, dict] = {}

        if strategy in ("mixed", "pixabay_only") and scene_units:
            prompt_texts = [
                self.image_prompts[index] if index < len(self.image_prompts) else self.image_prompts[-1]
                for index in range(len(scene_units))
            ]
            pixabay_assets_by_scene = self._plan_pixabay_scene_assets(
                scene_units,
                prompt_texts,
                estimated_scene_durations,
            )

        for scene_index, scene_text in enumerate(scene_units):
            prompt = self.image_prompts[scene_index] if scene_index < len(self.image_prompts) else self.image_prompts[-1]
            asset_created = scene_index in pixabay_assets_by_scene

            if not asset_created and strategy in ("mixed", "ai_only"):
                max_ai_assets = target_count if strategy == "ai_only" else min(get_max_ai_assets(), target_count)
                if ai_used < max_ai_assets:
                    try:
                        asset_created = bool(self.generate_image(prompt, scene_index=scene_index))
                        if asset_created:
                            ai_used += 1
                    except ImageRateLimitError:
                        raise

        return len(self.visual_assets)

    def _get_video_trim_window(self, clip_duration: float, target_duration: float, scene_index: int) -> tuple[float, float]:
        """
        Chooses a deterministic trim window inside a longer source video.

        Args:
            clip_duration (float): original clip duration
            target_duration (float): desired output duration
            scene_index (int): aligned scene order

        Returns:
            window (tuple[float, float]): start and end times
        """
        clip_duration = max(0.0, float(clip_duration or 0.0))
        target_duration = max(0.1, float(target_duration or 0.1))
        headroom = max(0.0, clip_duration - target_duration)
        if headroom <= 0:
            return 0.0, min(target_duration, clip_duration)

        anchor = ((scene_index % 5) + 1) / 6.0
        start_time = headroom * anchor
        return start_time, start_time + target_duration

    def _normalize_trimmed_pixabay_video(
        self,
        asset_path: str,
        start_time: float,
        duration: float,
        scene_index: int,
    ) -> str | None:
        """
        Re-encodes a trimmed Pixabay video window into a clean H.264/yuv420p
        clip before MoviePy loads it. This avoids intermittent black renders
        seen when seeking into some stock MP4s directly.

        Args:
            asset_path (str): original stock video path
            start_time (float): trim start in seconds
            duration (float): requested duration in seconds
            scene_index (int): aligned scene order

        Returns:
            normalized_path (str | None): safe trimmed clip path when successful
        """
        safe_stem = self._slugify_label(os.path.splitext(os.path.basename(asset_path))[0])
        start_ms = int(round(max(0.0, start_time) * 1000))
        duration_ms = int(round(max(0.1, duration) * 1000))
        normalized_path = self._get_workspace_path(
            f"{safe_stem}_pixabay_scene_{scene_index:02d}_{start_ms}_{duration_ms}.mp4"
        )

        if os.path.exists(normalized_path) and os.path.getsize(normalized_path) > 0:
            return normalized_path

        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            asset_path,
            "-ss",
            f"{max(0.0, start_time):.3f}",
            "-t",
            f"{max(0.1, duration):.3f}",
            "-an",
            "-vf",
            "fps=30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                normalized_path,
        ]

        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            if os.path.exists(normalized_path) and os.path.getsize(normalized_path) > 0:
                if get_verbose():
                    info(f' => Normalized Pixabay segment to "{normalized_path}"', show_emoji=False)
                return normalized_path
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            if get_verbose():
                warning(f'Failed to normalize Pixabay clip "{asset_path}": {exc}', show_emoji=False)

        return None

    def _build_visual_clip(self, asset: dict, duration: float):
        """
        Builds a MoviePy clip from an image or stock video asset.

        Args:
            asset (dict): asset descriptor
            duration (float): requested clip duration

        Returns:
            clip: MoviePy clip
        """
        asset_type = asset.get("type")
        asset_path = asset.get("path")
        asset_source = str(asset.get("source") or "").strip().lower()
        scene_index = int(asset.get("scene_index", 0) or 0)

        if asset_type == "video":
            working_asset_path = asset_path
            clip = VideoFileClip(working_asset_path).without_audio()
            if clip.duration >= duration:
                start_time, end_time = self._get_video_trim_window(clip.duration, duration, scene_index)
                trim_duration = max(0.1, end_time - start_time)

                # Pixabay clips occasionally decode fine as full files but render
                # black after an in-memory seek. Normalize the trimmed window first.
                if asset_source == "pixabay" and start_time > 0:
                    clip.close()
                    normalized_asset_path = self._normalize_trimmed_pixabay_video(
                        asset_path,
                        start_time=start_time,
                        duration=trim_duration,
                        scene_index=scene_index,
                    )
                    if normalized_asset_path:
                        working_asset_path = normalized_asset_path
                        clip = VideoFileClip(working_asset_path).without_audio()
                    else:
                        clip = VideoFileClip(asset_path).without_audio().subclip(start_time, end_time)
                else:
                    clip = clip.subclip(start_time, end_time)
            else:
                clip = clip.fx(vfx.loop, duration=duration)
            clip = clip.set_fps(30)
        else:
            clip = ImageClip(asset_path)
            clip = clip.set_duration(duration).set_fps(30)

        if round((clip.w / clip.h), 4) < 0.5625:
            if get_verbose():
                info(f' => Resizing Asset: {working_asset_path if asset_type == "video" else asset_path} to 1080x1920')
            clip = crop(
                clip,
                width=clip.w,
                height=round(clip.w / 0.5625),
                x_center=clip.w / 2,
                y_center=clip.h / 2,
            )
        else:
            if get_verbose():
                info(f' => Resizing Asset: {working_asset_path if asset_type == "video" else asset_path} to 1920x1080')
            clip = crop(
                clip,
                width=round(0.5625 * clip.h),
                height=clip.h,
                x_center=clip.w / 2,
                y_center=clip.h / 2,
            )

        clip = clip.resize((1080, 1920))

        if asset_type == "video":
            clip = self._stabilize_video_scene_clip(clip, duration)
        else:
            clip = self._apply_image_motion(clip, duration, scene_index)

        return clip

    def _stabilize_video_scene_clip(self, clip, duration: float):
        """
        Wraps a prepared video scene in a fixed-size composite clip so MoviePy
        does not have to concatenate mixed clip types directly.

        Args:
            clip: cropped/resized MoviePy video clip
            duration (float): requested scene duration

        Returns:
            clip: stabilized composite video clip
        """
        duration = max(0.1, float(duration or 0.1))

        return (
            CompositeVideoClip(
                [clip.set_position(("center", "center"))],
                size=(1080, 1920),
            )
            .set_duration(duration)
            .set_fps(30)
        )

    def _apply_image_motion(self, clip, duration: float, scene_index: int):
        """
        Applies a subtle Ken Burns-style move to still images so they feel
        more like living scenes than static slides.

        Args:
            clip: base MoviePy image clip already normalized to 1080x1920
            duration (float): scene duration
            scene_index (int): scene order

        Returns:
            clip: animated image clip
        """
        duration = max(0.35, float(duration or 0.35))
        canvas_w, canvas_h = 1080, 1920

        motion_patterns = [
            {"zoom_start": 1.08, "zoom_end": 1.16, "x_start": 0.00, "x_end": -70.0, "y_start": -10.0, "y_end": 20.0},
            {"zoom_start": 1.15, "zoom_end": 1.05, "x_start": -60.0, "x_end": 20.0, "y_start": -25.0, "y_end": 10.0},
            {"zoom_start": 1.10, "zoom_end": 1.18, "x_start": 25.0, "x_end": -25.0, "y_start": -40.0, "y_end": 40.0},
            {"zoom_start": 1.14, "zoom_end": 1.08, "x_start": -20.0, "x_end": 35.0, "y_start": 15.0, "y_end": -35.0},
            {"zoom_start": 1.07, "zoom_end": 1.14, "x_start": 0.0, "x_end": 0.0, "y_start": -60.0, "y_end": 35.0},
            {"zoom_start": 1.12, "zoom_end": 1.06, "x_start": -35.0, "x_end": 45.0, "y_start": 0.0, "y_end": 0.0},
        ]
        pattern = motion_patterns[scene_index % len(motion_patterns)]

        def lerp(start: float, end: float, progress: float) -> float:
            return start + ((end - start) * progress)

        def current_zoom(t: float) -> float:
            return lerp(
                pattern["zoom_start"],
                pattern["zoom_end"],
                min(1.0, max(0.0, t / duration)),
            )

        max_zoom = max(pattern["zoom_start"], pattern["zoom_end"])
        max_x_drift = max(0.0, ((canvas_w * max_zoom) - canvas_w) / 2)
        max_y_drift = max(0.0, ((canvas_h * max_zoom) - canvas_h) / 2)

        x_start = max(-max_x_drift, min(max_x_drift, pattern["x_start"]))
        x_end = max(-max_x_drift, min(max_x_drift, pattern["x_end"]))
        y_start = max(-max_y_drift, min(max_y_drift, pattern["y_start"]))
        y_end = max(-max_y_drift, min(max_y_drift, pattern["y_end"]))

        animated = clip.resize(lambda t: current_zoom(t))
        animated = animated.set_position(
            lambda t: (
                (canvas_w - (canvas_w * current_zoom(t))) / 2 + lerp(x_start, x_end, min(1.0, max(0.0, t / duration))),
                (canvas_h - (canvas_h * current_zoom(t))) / 2 + lerp(y_start, y_end, min(1.0, max(0.0, t / duration))),
            )
        )

        return CompositeVideoClip([animated], size=(canvas_w, canvas_h)).set_duration(duration).set_fps(30)

    def _get_scene_durations(self, total_duration: float) -> list[float]:
        """
        Estimates aligned scene durations from the ordered scene units.

        Args:
            total_duration (float): total voiceover duration

        Returns:
            durations (list[float]): per-scene durations
        """
        scene_units = self._get_scene_units()
        if not scene_units:
            return [max(total_duration, 0.1)]

        def scene_weight(unit: str) -> float:
            words = re.findall(r"\S+", str(unit or ""), flags=re.UNICODE)
            punctuation_pauses = len(re.findall(r"[،,؛;:.!?؟…]", str(unit or "")))
            return max(1.0, len(words) + (punctuation_pauses * 0.35))

        total_weight = sum(scene_weight(unit) for unit in scene_units)
        raw_durations = [
            max(0.35, total_duration * (scene_weight(unit) / total_weight))
            for unit in scene_units
        ]
        total_raw = sum(raw_durations) or total_duration or 1.0
        durations = [duration * (total_duration / total_raw) for duration in raw_durations]
        durations[-1] += max(0.0, total_duration - sum(durations))
        return durations

    def _get_scene_aligned_assets(self) -> list[dict]:
        """
        Orders visual assets against the current scene list, reusing the nearest
        available asset when some scenes failed to generate.

        Returns:
            ordered_assets (list[dict]): one asset per scene
        """
        scene_units = self._get_scene_units()
        if not scene_units:
            return self.visual_assets or [
                {"type": "image", "path": image_path, "source": "legacy", "scene_index": index}
                for index, image_path in enumerate(self.images)
            ]

        assets = self.visual_assets or [
            {"type": "image", "path": image_path, "source": "legacy", "scene_index": index}
            for index, image_path in enumerate(self.images)
        ]
        if not assets:
            return []

        assets_by_scene = {}
        fallback_assets = []
        for asset in assets:
            scene_index = asset.get("scene_index")
            if isinstance(scene_index, int):
                assets_by_scene.setdefault(scene_index, asset)
            fallback_assets.append(asset)

        ordered_assets = []
        last_asset = None
        for scene_index in range(len(scene_units)):
            asset = assets_by_scene.get(scene_index)
            if asset is None:
                asset = last_asset or fallback_assets[min(scene_index, len(fallback_assets) - 1)]
            ordered_assets.append(asset)
            last_asset = asset

        return ordered_assets

    def _parse_srt_entries_utf8(self, srt_path: str) -> list[tuple[tuple[float, float], str]]:
        """
        Loads an SRT file as UTF-8 and converts it into SubtitlesClip entries.

        Args:
            srt_path (str): subtitle file path

        Returns:
            entries (list[tuple[tuple[float, float], str]]): parsed subtitle entries
        """
        with open(srt_path, "r", encoding="utf-8") as file:
            raw = file.read().strip()

        if not raw:
            return []

        blocks = re.split(r"\r?\n\r?\n+", raw)
        entries: list[tuple[tuple[float, float], str]] = []

        for block in blocks:
            lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
            if len(lines) < 3:
                continue

            timing_line = lines[1] if "-->" in lines[1] else lines[0]
            text_lines = lines[2:] if "-->" in lines[1] else lines[1:]
            if "-->" not in timing_line:
                continue

            start_raw, end_raw = [part.strip() for part in timing_line.split("-->", 1)]
            entries.append(((self._srt_timestamp_to_seconds(start_raw), self._srt_timestamp_to_seconds(end_raw)), "\n".join(text_lines)))

        return entries

    def _srt_timestamp_to_seconds(self, value: str) -> float:
        """
        Converts an SRT timestamp into seconds.

        Args:
            value (str): SRT timestamp

        Returns:
            seconds (float): timestamp in seconds
        """
        hours, minutes, seconds = value.split(":")
        secs, millis = seconds.split(",")
        return (
            int(hours) * 3600
            + int(minutes) * 60
            + int(secs)
            + int(millis) / 1000
        )

    def generate_image_nanobanana2(self, prompt: str, scene_index: int | None = None) -> str:
        """
        Generates an AI Image using Nano Banana 2 API (Gemini image API).

        Args:
            prompt (str): Prompt for image generation

        Returns:
            path (str): The path to the generated image.
        """
        print(f"Generating Image using Nano Banana 2 API: {prompt}")

        api_key = get_nanobanana2_api_key()
        if not api_key:
            error("nanobanana2_api_key is not configured.")
            return None

        base_url = get_nanobanana2_api_base_url().rstrip("/")
        model = get_nanobanana2_model()
        aspect_ratio = get_nanobanana2_aspect_ratio()

        endpoint = f"{base_url}/models/{model}:generateContent"

        def build_payload(prompt_text: str) -> dict:
            return {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {"aspectRatio": aspect_ratio},
                },
            }

        prompt_variants = [
            prompt,
            self._make_provider_friendly_image_prompt(prompt),
        ]

        for prompt_index, prompt_variant in enumerate(prompt_variants, start=1):
            payload = build_payload(prompt_variant)
            if prompt_index > 1 and get_verbose():
                warning("Retrying Nano Banana with a shorter provider-friendly prompt.")

            for attempt in range(1, IMAGE_RATE_LIMIT_RETRIES + 2):
                try:
                    response = requests.post(
                        endpoint,
                        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                        json=payload,
                        timeout=300,
                    )

                    if _is_retryable_rate_limit(response):
                        if attempt > IMAGE_RATE_LIMIT_RETRIES:
                            raise ImageRateLimitError(
                                "Nano Banana 2 image generation hit the API rate limit. "
                                "Wait a bit, reduce prompt volume, or use a higher quota before trying again."
                            )

                        retry_after = response.headers.get("Retry-After")
                        wait_seconds = (
                            int(retry_after)
                            if retry_after and retry_after.isdigit()
                            else IMAGE_RATE_LIMIT_BACKOFF_SECONDS * attempt
                        )
                        warning(
                            f"Image API rate-limited. Waiting {wait_seconds}s before retry {attempt}/{IMAGE_RATE_LIMIT_RETRIES}."
                        )
                        time.sleep(wait_seconds)
                        continue

                    response.raise_for_status()
                    body = response.json()

                    candidates = body.get("candidates", [])
                    for candidate in candidates:
                        content = candidate.get("content", {})
                        for part in content.get("parts", []):
                            inline_data = part.get("inlineData") or part.get("inline_data")
                            if not inline_data:
                                continue
                            data = inline_data.get("data")
                            mime_type = inline_data.get("mimeType") or inline_data.get("mime_type", "")
                            if data and str(mime_type).startswith("image/"):
                                image_bytes = base64.b64decode(data)
                                path = self._persist_image(image_bytes, "Nano Banana 2 API", scene_index=scene_index)
                                self._record_image_generation_cost(
                                    "nanobanana2",
                                    get_nanobanana2_model(),
                                    scene_index=scene_index,
                                )
                                return path

                    if _nanobanana_response_has_no_image(body) and prompt_index < len(prompt_variants):
                        break

                    if get_verbose():
                        warning(f"Nano Banana 2 did not return an image payload. Response: {body}")
                    return None
                except Exception as e:
                    if isinstance(e, ImageRateLimitError):
                        raise
                    if get_verbose():
                        warning(f"Failed to generate image with Nano Banana 2 API: {str(e)}")
                    return None

        return None

    def _get_openai_image_size(self) -> str:
        """
        Maps the configured aspect ratio to an OpenAI-supported image size.

        Returns:
            size (str): image size string
        """
        aspect_ratio = get_nanobanana2_aspect_ratio()
        size_map = {
            "1:1": "1024x1024",
            "9:16": "1024x1536",
            "16:9": "1536x1024",
        }
        return size_map.get(aspect_ratio, "1024x1536")

    def _decode_data_url_image(self, data_url: str) -> bytes | None:
        """
        Decodes a data URL containing image bytes.

        Args:
            data_url (str): data URL payload

        Returns:
            image_bytes (bytes | None): decoded image bytes if available
        """
        if not data_url or not isinstance(data_url, str):
            return None

        if not data_url.startswith("data:"):
            return None

        try:
            _, encoded = data_url.split(",", 1)
        except ValueError:
            return None

        return base64.b64decode(encoded)

    def _get_openrouter_modalities(self, model_name: str) -> list[str]:
        """
        Returns the correct OpenRouter modalities for the selected image model.

        Flux and Sourceful models are image-only, while Gemini-style image
        models can return both text and images.

        Args:
            model_name (str): OpenRouter model id

        Returns:
            modalities (list[str]): request modalities
        """
        normalized = str(model_name or "").strip().lower()
        image_only_prefixes = (
            "black-forest-labs/flux",
            "sourceful/",
        )

        if normalized.startswith(image_only_prefixes):
            return ["image"]

        return ["image", "text"]

    def generate_image_openai(self, prompt: str, scene_index: int | None = None) -> str:
        """
        Generates an AI image using OpenAI's Images API.

        Args:
            prompt (str): Prompt for image generation

        Returns:
            path (str): The path to the generated image.
        """
        print(f"Generating Image using OpenAI Images API: {prompt}")

        api_key = get_openai_api_key()
        if not api_key:
            error("openai_api_key is not configured.")
            return None

        endpoint = f"{get_openai_base_url().rstrip('/')}/images/generations"
        payload = {
            "model": get_openai_image_model(),
            "prompt": prompt,
            "size": self._get_openai_image_size(),
            "quality": get_openai_image_quality(),
            "n": 1,
            "output_format": "png",
        }

        for attempt in range(1, IMAGE_RATE_LIMIT_RETRIES + 2):
            try:
                response = requests.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=300,
                )

                if _is_retryable_rate_limit(response):
                    if attempt > IMAGE_RATE_LIMIT_RETRIES:
                        raise ImageRateLimitError(
                            "OpenAI image generation hit the API rate limit. "
                            "Wait a bit, reduce prompt volume, or use a higher quota before trying again."
                        )

                    retry_after = response.headers.get("Retry-After")
                    wait_seconds = (
                        int(retry_after)
                        if retry_after and retry_after.isdigit()
                        else IMAGE_RATE_LIMIT_BACKOFF_SECONDS * attempt
                    )
                    warning(
                        f"OpenAI image API rate-limited. Waiting {wait_seconds}s before retry {attempt}/{IMAGE_RATE_LIMIT_RETRIES}."
                    )
                    time.sleep(wait_seconds)
                    continue

                response.raise_for_status()
                body = response.json()
                images = body.get("data", [])
                if not images:
                    if get_verbose():
                        warning(f"OpenAI Images API did not return image data. Response: {body}")
                    return None

                b64_image = images[0].get("b64_json")
                if not b64_image:
                    if get_verbose():
                        warning(f"OpenAI Images API returned data without b64_json. Response: {body}")
                    return None

                image_bytes = base64.b64decode(b64_image)
                path = self._persist_image(image_bytes, "OpenAI Images API", scene_index=scene_index)
                self._record_image_generation_cost(
                    "openai",
                    get_openai_image_model(),
                    scene_index=scene_index,
                )
                return path
            except Exception as e:
                if isinstance(e, ImageRateLimitError):
                    raise
                if get_verbose():
                    warning(f"Failed to generate image with OpenAI Images API: {str(e)}")
                return None

    def generate_image_openrouter(self, prompt: str, scene_index: int | None = None) -> str:
        """
        Generates an AI image using OpenRouter's image-capable chat API.

        Args:
            prompt (str): Prompt for image generation

        Returns:
            path (str): The path to the generated image.
        """
        print(f"Generating Image using OpenRouter: {prompt}")

        api_key = get_openrouter_api_key()
        if not api_key:
            error("openrouter_api_key is not configured.")
            return None

        endpoint = "https://openrouter.ai/api/v1/chat/completions"
        model_name = get_openrouter_image_model()
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": self._get_openrouter_modalities(model_name),
            "image_config": {"aspect_ratio": get_nanobanana2_aspect_ratio()},
        }

        for attempt in range(1, IMAGE_RATE_LIMIT_RETRIES + 2):
            try:
                response = requests.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://openrouter.ai",
                        "X-Title": "MoneyPrinterV2",
                    },
                    json=payload,
                    timeout=300,
                )

                if _is_retryable_rate_limit(response):
                    if attempt > IMAGE_RATE_LIMIT_RETRIES:
                        raise ImageRateLimitError(
                            "OpenRouter image generation hit the API rate limit. "
                            "Wait a bit, reduce prompt volume, or use a higher quota before trying again."
                        )

                    retry_after = response.headers.get("Retry-After")
                    wait_seconds = (
                        int(retry_after)
                        if retry_after and retry_after.isdigit()
                        else IMAGE_RATE_LIMIT_BACKOFF_SECONDS * attempt
                    )
                    warning(
                        f"OpenRouter image API rate-limited. Waiting {wait_seconds}s before retry {attempt}/{IMAGE_RATE_LIMIT_RETRIES}."
                    )
                    time.sleep(wait_seconds)
                    continue

                try:
                    response.raise_for_status()
                except requests.HTTPError as exc:
                    error_details = response.text.strip()
                    if error_details:
                        raise RuntimeError(
                            f"OpenRouter image request failed with status {response.status_code}: {error_details}"
                        ) from exc
                    raise

                body = response.json()
                choices = body.get("choices", [])
                if not choices:
                    if get_verbose():
                        warning(f"OpenRouter did not return image choices. Response: {body}")
                    return None

                message = choices[0].get("message", {})
                images = message.get("images", [])
                if not images:
                    if get_verbose():
                        warning(f"OpenRouter did not return images in the response. Response: {body}")
                    return None

                first_image = images[0]
                image_url = first_image.get("url")

                if not image_url:
                    image_url_field = first_image.get("image_url")
                    if isinstance(image_url_field, dict):
                        image_url = image_url_field.get("url")
                    elif isinstance(image_url_field, str):
                        image_url = image_url_field

                if not image_url:
                    image_url_field = first_image.get("imageUrl")
                    if isinstance(image_url_field, dict):
                        image_url = image_url_field.get("url")
                    elif isinstance(image_url_field, str):
                        image_url = image_url_field

                image_bytes = self._decode_data_url_image(image_url)

                if image_bytes is None and image_url:
                    downloaded = requests.get(image_url, timeout=300)
                    downloaded.raise_for_status()
                    image_bytes = downloaded.content

                if image_bytes is None:
                    if get_verbose():
                        warning(f"OpenRouter returned an unsupported image payload. Response: {body}")
                    return None

                path = self._persist_image(image_bytes, "OpenRouter", scene_index=scene_index)
                self._record_image_generation_cost(
                    "openrouter",
                    model_name,
                    scene_index=scene_index,
                )
                return path
            except Exception as e:
                if isinstance(e, ImageRateLimitError):
                    raise
                if get_verbose():
                    warning(f"Failed to generate image with OpenRouter: {str(e)}")
                return None

    def generate_image(self, prompt: str, scene_index: int | None = None) -> str:
        """
        Generates an AI image based on the configured provider.

        Args:
            prompt (str): Reference for image generation

        Returns:
            path (str): The path to the generated image.
        """
        provider = get_image_provider()

        if provider == "openai":
            return self.generate_image_openai(prompt, scene_index=scene_index)

        if provider == "openrouter":
            return self.generate_image_openrouter(prompt, scene_index=scene_index)

        return self.generate_image_nanobanana2(prompt, scene_index=scene_index)

    def generate_script_to_speech(self, tts_instance: TTS) -> str:
        """
        Converts the generated script into Speech using KittenTTS and returns the path to the wav file.

        Args:
            tts_instance (tts): Instance of TTS Class.

        Returns:
            path_to_wav (str): Path to generated audio (WAV Format).
        """
        path = self._get_workspace_path("voiceover.wav")
        tts_text = str(self.script or "").strip()
        if not tts_text:
            raise RuntimeError("Cannot generate TTS without a script.")

        tts_instance.synthesize(
            tts_text,
            path,
            language=self.language,
            dialect=self.dialect,
            cost_callback=self._record_tts_cost,
        )

        self.tts_path = path

        if get_verbose():
            info(f' => Wrote TTS to "{path}"')

        return path

    def add_video(self, video: dict) -> None:
        """
        Adds a video to the cache.

        Args:
            video (dict): The video to add

        Returns:
            None
        """
        cache = get_youtube_cache_path()
        with open(cache, "r", encoding="utf-8-sig") as file:
            previous_json = json.load(file)

            # Find our account
            accounts = previous_json["accounts"]
            for account in accounts:
                if account["id"] == self._account_uuid:
                    account["videos"].append(video)
                    break

            # Commit changes
            with open(cache, "w", encoding="utf-8") as file:
                json.dump(previous_json, file, indent=4, ensure_ascii=False)

    def generate_subtitles(self, audio_path: str) -> str:
        """
        Generates subtitles for the audio directly from the current script.

        Args:
            audio_path (str): The path to the audio file.

        Returns:
            path (str): The path to the generated SRT File.
        """
        if str(self.script or "").strip():
            return self.generate_subtitles_from_script(audio_path)

        provider = str(get_stt_provider() or "local_whisper").lower()

        if provider == "local_whisper":
            return self.generate_subtitles_local_whisper(audio_path)

        if provider == "third_party_assemblyai":
            return self.generate_subtitles_assemblyai(audio_path)

        warning(f"Unknown stt_provider '{provider}'. Falling back to local_whisper.")
        return self.generate_subtitles_local_whisper(audio_path)

    def generate_subtitles_assemblyai(self, audio_path: str) -> str:
        """
        Generates subtitles using AssemblyAI.

        Args:
            audio_path (str): Audio file path

        Returns:
            path (str): Path to SRT file
        """
        aai.settings.api_key = get_assemblyai_api_key()
        config = aai.TranscriptionConfig()
        transcriber = aai.Transcriber(config=config)
        transcript = transcriber.transcribe(audio_path)
        subtitles = transcript.export_subtitles_srt()

        srt_path = self._get_workspace_path("subtitles.srt")

        with open(srt_path, "w", encoding="utf-8") as file:
            file.write(subtitles)

        self._record_stt_cost("third_party_assemblyai", audio_path, label="AssemblyAI subtitles")
        return srt_path

    def _get_transcription_language_code(self) -> str | None:
        """
        Maps the configured content language to a transcription language code.

        Returns:
            code (str | None): Whisper-compatible language code or None for auto
        """
        normalized = str(self.language or "").strip().lower()
        language_map = {
            "arabic": "ar",
            "egyptian arabic": "ar",
            "gulf arabic": "ar",
            "levantine arabic": "ar",
            "saudi arabic": "ar",
            "moroccan darija": "ar",
            "darija": "ar",
            "english": "en",
        }
        return language_map.get(normalized)

    def _contains_arabic_text(self, value: str) -> bool:
        """
        Returns whether the provided text contains Arabic script.

        Args:
            value (str): text to inspect

        Returns:
            is_arabic (bool): True when Arabic characters are present
        """
        return bool(re.search(r"[\u0600-\u06FF]", str(value or "")))

    def _get_subtitle_font_filename_for_text(self, text: str) -> str:
        """
        Resolves the subtitle font filename based on the subtitle text.

        Args:
            text (str): subtitle text

        Returns:
            font_filename (str): best subtitle font filename
        """
        preferred_font = (
            get_subtitle_font_arabic()
            if self._contains_arabic_text(text) or self._contains_arabic_text(getattr(self, "script", ""))
            else get_subtitle_font_english()
        )
        fonts_dir = get_fonts_dir()
        preferred_path = os.path.join(fonts_dir, preferred_font)
        if os.path.exists(preferred_path):
            return preferred_path

        fallback_font = get_subtitle_font()
        fallback_path = os.path.join(fonts_dir, fallback_font)
        if os.path.exists(fallback_path):
            return fallback_path

        legacy_path = os.path.join(fonts_dir, get_font())
        return legacy_path

    def _get_explicit_arabic_subtitle_font_path(self) -> str:
        """
        Returns the configured Arabic subtitle font path without falling back
        to the English font selector.

        Returns:
            path (str): Arabic subtitle font path
        """
        fonts_dir = get_fonts_dir()
        preferred_font = get_subtitle_font_arabic()
        preferred_path = os.path.join(fonts_dir, preferred_font)
        if os.path.exists(preferred_path):
            return preferred_path

        fallback_font = get_subtitle_font()
        fallback_path = os.path.join(fonts_dir, fallback_font)
        if os.path.exists(fallback_path):
            warning(
                f"Configured Arabic subtitle font '{preferred_font}' was not found. Falling back to '{fallback_font}'."
            )
            return fallback_path

        legacy_path = os.path.join(fonts_dir, get_font())
        return legacy_path

    def _shape_subtitle_text_for_rendering(self, text: str) -> str:
        """
        Normalizes subtitle text before drawing. For Arabic, we keep the raw
        text and rely on RTL-aware drawing options when available.

        Args:
            text (str): subtitle text

        Returns:
            shaped (str): render-ready subtitle text
        """
        raw_text = str(text or "").strip()
        return re.sub(r"\s+", " ", raw_text).strip()

    def _get_pil_subtitle_draw_kwargs(self, text: str) -> dict:
        """
        Returns PIL drawing kwargs for subtitle rendering.

        Args:
            text (str): subtitle text

        Returns:
            kwargs (dict): PIL text drawing options
        """
        if self._contains_arabic_text(text):
            return {"direction": "rtl", "language": "ar"}
        return {}

    def _fallback_rtl_visual_order(self, text: str) -> str:
        """
        Fallback for environments where RTL drawing support is unavailable.
        It reverses each line so Arabic can still appear in readable order in
        basic left-to-right renderers.

        Args:
            text (str): subtitle text

        Returns:
            reordered (str): visually reordered text
        """
        lines = str(text or "").splitlines() or [str(text or "")]
        return "\n".join(line[::-1] for line in lines)

    def _load_subtitle_font(self, text: str):
        """
        Loads the configured subtitle font for the given text.

        Args:
            text (str): subtitle text

        Returns:
            font (ImageFont.FreeTypeFont): loaded font
        """
        font_path = self._get_explicit_arabic_subtitle_font_path()
        font_size = max(24, int(get_subtitle_font_size()))
        try:
            return ImageFont.truetype(font_path, font_size)
        except Exception:
            return ImageFont.load_default()

    def _wrap_subtitle_text(
        self,
        text: str,
        font,
        max_width: int,
        stroke_width: int,
    ) -> tuple[str, dict]:
        """
        Wraps subtitle text to fit a target width using PIL measurements.

        Args:
            text (str): subtitle text
            font: PIL font
            max_width (int): maximum rendered width
            stroke_width (int): subtitle stroke width

        Returns:
            wrapped (tuple[str, dict]): wrapped subtitle text and draw kwargs
        """
        raw = str(text or "").strip()
        if not raw:
            return "", {}

        drawer = ImageDraw.Draw(Image.new("RGBA", (10, 10), (0, 0, 0, 0)))
        words = raw.split()
        if len(words) <= 1:
            try:
                drawer.multiline_textbbox(
                    (0, 0),
                    raw,
                    font=font,
                    stroke_width=stroke_width,
                    spacing=10,
                    align="center",
                    **self._get_pil_subtitle_draw_kwargs(raw),
                )
                return raw, self._get_pil_subtitle_draw_kwargs(raw)
            except Exception:
                fallback = self._fallback_rtl_visual_order(raw)
                return fallback, {}

        lines: list[str] = []
        current = words[0]
        draw_kwargs = self._get_pil_subtitle_draw_kwargs(raw)
        for word in words[1:]:
            candidate = f"{current} {word}".strip()
            try:
                bbox = drawer.multiline_textbbox(
                    (0, 0),
                    candidate,
                    font=font,
                    stroke_width=stroke_width,
                    spacing=10,
                    align="center",
                    **draw_kwargs,
                )
            except Exception:
                draw_kwargs = {}
                candidate = self._fallback_rtl_visual_order(candidate)
                bbox = drawer.multiline_textbbox(
                    (0, 0),
                    candidate,
                    font=font,
                    stroke_width=stroke_width,
                    spacing=10,
                    align="center",
                )
            if (bbox[2] - bbox[0]) <= max_width:
                current = candidate if draw_kwargs else self._fallback_rtl_visual_order(f"{current} {word}".strip())
                continue
            lines.append(current)
            current = word

        if current:
            lines.append(current)

        wrapped = "\n".join(lines)
        if not draw_kwargs and self._contains_arabic_text(raw):
            wrapped = self._fallback_rtl_visual_order(raw)
        return wrapped, draw_kwargs

    def _create_subtitle_bitmap(self, text: str) -> np.ndarray:
        """
        Renders one subtitle unit into an RGBA bitmap using PIL so the chosen
        local font files are actually respected.

        Args:
            text (str): subtitle text

        Returns:
            image (np.ndarray): RGBA subtitle bitmap
        """
        shaped_text = self._shape_subtitle_text_for_rendering(text)
        stroke_width = max(0, int(get_subtitle_stroke_width()))
        text_image = self._render_subtitle_text_image(
            shaped_text,
            max_width=900,
            stroke_width=stroke_width,
        )
        text_width = max(1, int(text_image.width))
        text_height = max(1, int(text_image.height))
        pad_x = 48
        pad_y = 30
        image = Image.new(
            "RGBA",
            (text_width + pad_x * 2, text_height + pad_y * 2),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (0, 0, image.width - 1, image.height - 1),
            radius=28,
            fill=(0, 0, 0, 150),
        )
        image.alpha_composite(
            text_image,
            (
                int((image.width - text_image.width) / 2),
                int((image.height - text_image.height) / 2),
            ),
        )
        return np.array(image)

    def _render_subtitle_text_image(
        self,
        text: str,
        max_width: int,
        stroke_width: int,
    ) -> Image.Image:
        """
        Renders subtitle text to a transparent image. Arabic text uses
        ImageMagick for proper shaping and RTL layout; other text uses PIL.

        Args:
            text (str): subtitle text
            max_width (int): maximum text width before wrapping
            stroke_width (int): outline thickness

        Returns:
            image (Image.Image): transparent RGBA text image
        """
        if self._contains_arabic_text(text):
            try:
                return self._render_arabic_subtitle_with_imagemagick(
                    text,
                    max_width=max_width,
                    stroke_width=stroke_width,
                )
            except Exception as exc:
                warning(f"Falling back to PIL subtitle rendering for Arabic text: {exc}")

        font = self._load_subtitle_font(text)
        wrapped_text, draw_kwargs = self._wrap_subtitle_text(
            text,
            font,
            max_width=max_width,
            stroke_width=stroke_width,
        )

        drawer = ImageDraw.Draw(Image.new("RGBA", (10, 10), (0, 0, 0, 0)))
        bbox = drawer.multiline_textbbox(
            (0, 0),
            wrapped_text,
            font=font,
            stroke_width=stroke_width,
            spacing=12,
            align="center",
            **draw_kwargs,
        )
        text_width = max(1, int(bbox[2] - bbox[0]))
        text_height = max(1, int(bbox[3] - bbox[1]))
        text_image = Image.new("RGBA", (text_width, text_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(text_image)
        draw.multiline_text(
            (text_width / 2, text_height / 2),
            wrapped_text,
            font=font,
            fill=get_subtitle_color(),
            stroke_fill=get_subtitle_stroke_color(),
            stroke_width=stroke_width,
            anchor="mm",
            align="center",
            spacing=12,
            **draw_kwargs,
        )
        return text_image

    def _render_arabic_subtitle_with_imagemagick(
        self,
        text: str,
        max_width: int,
        stroke_width: int,
    ) -> Image.Image:
        """
        Uses ImageMagick's Arabic-capable text rendering delegates to draw
        Arabic subtitles with connected letters and proper RTL shaping.

        Args:
            text (str): subtitle text
            max_width (int): text wrapping width
            stroke_width (int): outline thickness

        Returns:
            image (Image.Image): transparent RGBA text image
        """
        magick_path = get_imagemagick_path()
        font_path = self._get_subtitle_font_filename_for_text(text)
        point_size = max(24, int(get_subtitle_font_size()))
        rgba_fill = str(get_subtitle_color())
        render_text = self._shape_subtitle_text_for_rendering(text)

        common = [
            magick_path,
            "-background",
            "none",
            "-fill",
            rgba_fill,
            "-stroke",
            "none",
            "-strokewidth",
            "0",
            "-font",
            font_path,
            "-pointsize",
            str(point_size),
            "-gravity",
            "center",
        ]

        label_args = [*common, f"label:{render_text}", "png:-"]
        result = subprocess.run(label_args, capture_output=True, check=True)
        image = Image.open(io.BytesIO(result.stdout)).convert("RGBA")
        if image.width <= max_width:
            return image

        caption_args = [
            *common,
            "-size",
            f"{max_width}x",
            f"caption:{render_text}",
            "png:-",
        ]
        wrapped = subprocess.run(caption_args, capture_output=True, check=True)
        return Image.open(io.BytesIO(wrapped.stdout)).convert("RGBA")

    def _build_subtitle_overlay_clips(
        self,
        subtitle_entries: list[tuple[tuple[float, float], str]],
    ) -> list[ImageClip]:
        """
        Converts parsed subtitle entries into positioned overlay ImageClips.

        Args:
            subtitle_entries (list[tuple[tuple[float, float], str]]): timed subtitles

        Returns:
            overlay_clips (list[ImageClip]): subtitle overlay clips
        """
        overlay_clips = []
        subtitle_y = 1510 if get_subtitle_mode() == "word_by_word" else 1450

        for (start_time, end_time), text in subtitle_entries:
            duration = max(0.05, float(end_time) - float(start_time))
            rgba = self._create_subtitle_bitmap(text)
            rgb = rgba[:, :, :3]
            alpha = rgba[:, :, 3].astype("float32") / 255.0
            clip = (
                ImageClip(rgb)
                .set_mask(ImageClip(alpha, ismask=True))
                .set_start(float(start_time))
                .set_duration(duration)
                .set_pos(("center", subtitle_y))
            )
            overlay_clips.append(clip)

        return overlay_clips

    def _should_equalize_subtitles(self) -> bool:
        """
        Returns whether subtitle equalization should be applied.

        Arabic subtitles tend to degrade when aggressively reflowed into
        extremely short lines, so we skip equalization for Arabic content.

        Returns:
            should_equalize (bool): True when equalization is safe/useful
        """
        normalized = str(self.language or "").strip().lower()
        return "arabic" not in normalized and "darija" not in normalized

    def _should_use_script_subtitles(self) -> bool:
        """
        Returns whether subtitles should be generated directly from the script.

        Returns:
            use_script_subtitles (bool): True when script subtitles are preferred
        """
        return True

    def _split_script_into_subtitle_chunks(self) -> list[str]:
        """
        Splits the generated script into subtitle-sized chunks.

        Returns:
            chunks (list[str]): subtitle text chunks
        """
        raw_parts = re.split(r"(?<=[\.\!\?؟،,:;])\s+|\n+", str(self.script or "").strip())
        chunks = [part.strip(" \t\r\n-") for part in raw_parts if part.strip()]

        if chunks:
            return chunks

        fallback_words = str(self.script or "").split()
        chunk_size = 6
        return [
            " ".join(fallback_words[index:index + chunk_size]).strip()
            for index in range(0, len(fallback_words), chunk_size)
            if " ".join(fallback_words[index:index + chunk_size]).strip()
        ]

    def _split_script_into_subtitle_words(self) -> list[str]:
        """
        Splits the generated script into word-by-word subtitle units.

        Returns:
            words (list[str]): subtitle words
        """
        return re.findall(r"\S+", str(self.script or "").strip(), flags=re.UNICODE)

    def _get_subtitle_script_units(self) -> list[str]:
        """
        Returns subtitle timing units based on the configured subtitle mode.

        Returns:
            units (list[str]): subtitle units
        """
        if get_subtitle_mode() == "word_by_word":
            words = self._split_script_into_subtitle_words()
            if words:
                return words

        return self._split_script_into_subtitle_chunks()

    def generate_subtitles_from_script(self, audio_path: str) -> str:
        """
        Generates subtitles directly from the script text.

        Args:
            audio_path (str): Audio file path

        Returns:
            path (str): Path to SRT file
        """
        subtitle_units = self._get_subtitle_script_units()
        if not subtitle_units:
            raise RuntimeError("Cannot generate script-based subtitles without a script.")

        audio_clip = AudioFileClip(audio_path)
        total_duration = max(audio_clip.duration, 0.1)
        total_chars = sum(len(unit) for unit in subtitle_units) or len(subtitle_units)

        lines = []
        current_time = 0.0
        min_duration = 0.1 if get_subtitle_mode() == "word_by_word" else 0.8
        for idx, unit in enumerate(subtitle_units, start=1):
            unit_weight = len(unit) / total_chars if total_chars else 1 / len(subtitle_units)
            duration = max(total_duration * unit_weight, min_duration)
            end_time = min(total_duration, current_time + duration)

            if idx == len(subtitle_units):
                end_time = total_duration

            lines.append(str(idx))
            lines.append(
                f"{self._format_srt_timestamp(current_time)} --> {self._format_srt_timestamp(end_time)}"
            )
            lines.append(unit)
            lines.append("")
            current_time = end_time

        subtitles = "\n".join(lines)
        srt_path = self._get_workspace_path("subtitles.srt")
        with open(srt_path, "w", encoding="utf-8") as file:
            file.write(subtitles)

        audio_clip.close()
        self._record_stt_cost("script_based", audio_path, label="Script-based subtitles")
        return srt_path

    def _format_srt_timestamp(self, seconds: float) -> str:
        """
        Formats a timestamp in seconds to SRT format.

        Args:
            seconds (float): Seconds

        Returns:
            ts (str): HH:MM:SS,mmm
        """
        total_millis = max(0, int(round(seconds * 1000)))
        hours = total_millis // 3600000
        minutes = (total_millis % 3600000) // 60000
        secs = (total_millis % 60000) // 1000
        millis = total_millis % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def generate_subtitles_local_whisper(self, audio_path: str) -> str:
        """
        Generates subtitles using local Whisper (faster-whisper).

        Args:
            audio_path (str): Audio file path

        Returns:
            path (str): Path to SRT file
        """
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            error(
                "Local STT selected but 'faster-whisper' is not installed. "
                "Install it or switch stt_provider to third_party_assemblyai."
            )
            raise

        model = WhisperModel(
            get_whisper_model(),
            device=get_whisper_device(),
            compute_type=get_whisper_compute_type(),
        )
        transcription_language = self._get_transcription_language_code()
        transcribe_kwargs = {"vad_filter": True, "task": "transcribe"}
        if transcription_language:
            transcribe_kwargs["language"] = transcription_language

        segments, _ = model.transcribe(audio_path, **transcribe_kwargs)

        lines = []
        for idx, segment in enumerate(segments, start=1):
            start = self._format_srt_timestamp(segment.start)
            end = self._format_srt_timestamp(segment.end)
            text = str(segment.text).strip()

            if not text:
                continue

            lines.append(str(idx))
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")

        subtitles = "\n".join(lines)
        srt_path = self._get_workspace_path("subtitles.srt")
        with open(srt_path, "w", encoding="utf-8") as file:
            file.write(subtitles)

        self._record_stt_cost("local_whisper", audio_path, get_whisper_model(), "Local Whisper subtitles")
        return srt_path

    def combine(self) -> str:
        """
        Combines everything into the final video.

        Returns:
            path (str): The path to the generated MP4 File.
        """
        assets = self._get_scene_aligned_assets()

        if len(assets) == 0:
            raise RuntimeError(
                "No visual assets were generated, so the video cannot be combined."
            )

        combined_image_path = self._get_workspace_path("final_video.mp4")
        threads = get_threads()
        tts_clip = AudioFileClip(self.tts_path)
        max_duration = tts_clip.duration
        scene_durations = self._get_scene_durations(max_duration)

        print(colored("[+] Combining visual assets...", "blue"))

        clips = []
        for asset, scene_duration in zip(assets, scene_durations):
            clip = self._build_visual_clip(asset, scene_duration)
            clips.append(clip)

        final_clip = concatenate_videoclips(clips, method="compose")
        final_clip = final_clip.set_fps(30)

        subtitle_overlay_clips = []
        try:
            subtitles_path = self.generate_subtitles(self.tts_path)
            if self._should_equalize_subtitles():
                equalize_subtitles(subtitles_path, 18)
            subtitle_entries = self._parse_srt_entries_utf8(subtitles_path)
            if not subtitle_entries:
                raise RuntimeError("Subtitle file was empty after generation.")
            subtitle_overlay_clips = self._build_subtitle_overlay_clips(subtitle_entries)
        except Exception as e:
            warning(f"Failed to generate subtitles, continuing without subtitles: {e}")

        final_audio = tts_clip.set_fps(44100)
        try:
            random_song = choose_random_song()
            random_song_clip = AudioFileClip(random_song).set_fps(44100)

            # Turn down volume
            random_song_clip = random_song_clip.fx(afx.volumex, 0.1)
            final_audio = CompositeAudioClip([final_audio, random_song_clip])
        except Exception as e:
            warning(f"Failed to add background song, continuing with voiceover only: {e}")

        final_clip = final_clip.set_audio(final_audio)
        final_clip = final_clip.set_duration(tts_clip.duration)

        if subtitle_overlay_clips:
            final_clip = CompositeVideoClip([final_clip, *subtitle_overlay_clips])

        final_clip.write_videofile(
            combined_image_path,
            threads=threads,
            codec="libx264",
            audio_codec="aac",
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        )

        success(f'Wrote Video to "{combined_image_path}"')

        return combined_image_path

    def _render_video_assets(self, tts_instance: TTS) -> str:
        """
        Generates images, audio, and the final video from the current subject
        and script state.

        Args:
            tts_instance (TTS): Instance of TTS Class.

        Returns:
            path (str): The path to the generated MP4 File.
        """
        self.images = []
        self.visual_assets = []
        self._cost_items = []
        self._cost_notes = []
        self._pixabay_selection_debug = []

        # Generate the Metadata
        self.generate_metadata()

        # Generate the Image Prompts
        self.generate_prompts()

        generated_assets = self._generate_visual_assets()

        if generated_assets == 0:
            raise RuntimeError(
                "Visual asset generation failed for all prompts. Check your AI image quota and Pixabay configuration, then try again."
            )

        if generated_assets < len(self.image_prompts):
            warning(
                f"Generated {generated_assets}/{len(self.image_prompts)} visual assets. Continuing with the successful ones."
            )

        # Generate the TTS
        self.generate_script_to_speech(tts_instance)

        # Combine everything
        path = self.combine()

        if get_verbose():
            info(f" => Generated Video: {path}")

        self.video_path = os.path.abspath(path)
        pricing_report = self._write_pricing_report()
        if isinstance(getattr(self, "metadata", None), dict):
            self.metadata["pricing"] = pricing_report

        return path

    def generate_video(self, tts_instance: TTS) -> str:
        """
        Generates a YouTube Short based on the provided niche and language.

        Args:
            tts_instance (TTS): Instance of TTS Class.

        Returns:
            path (str): The path to the generated MP4 File.
        """
        # Generate the Topic
        self.generate_topic()

        # Generate the Script
        self.generate_script()

        return self._render_video_assets(tts_instance)

    def generate_video_from_existing_script(
        self,
        tts_instance: TTS,
        script: str,
        subject: str = "",
    ) -> str:
        """
        Generates a video while using a caller-provided script.

        Args:
            tts_instance (TTS): Instance of TTS Class.
            script (str): Manual script to narrate
            subject (str): Optional subject/topic override

        Returns:
            path (str): The path to the generated MP4 File.
        """
        self.set_manual_script(script, subject)
        return self._render_video_assets(tts_instance)

    def get_channel_id(self) -> str:
        """
        Gets the Channel ID of the YouTube Account.

        Returns:
            channel_id (str): The Channel ID.
        """
        if self.browser is None:
            raise RuntimeError(
                "YouTube browser session is not initialized. Recreate this account session with browser access enabled."
            )

        driver = self.browser
        driver.get("https://studio.youtube.com")
        time.sleep(2)
        channel_id = driver.current_url.split("/")[-1]
        self.channel_id = channel_id

        return channel_id

    def _replace_textbox_value(self, element, text: str) -> None:
        """
        Replaces the text content of a YouTube Studio textbox.

        Args:
            element: Selenium web element for the textbox
            text (str): New text to enter

        Returns:
            None
        """
        driver = self.browser
        if driver is None:
            raise RuntimeError("Browser session is not initialized.")

        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});",
            element,
        )
        element.click()
        time.sleep(0.5)
        element.send_keys(Keys.CONTROL, "a")
        time.sleep(0.2)
        element.send_keys(Keys.DELETE)
        time.sleep(0.2)
        element.send_keys(text)

    def _click_element(self, element) -> None:
        """
        Clicks a Selenium element with a JavaScript fallback for Studio dialogs.

        Args:
            element: Selenium element

        Returns:
            None
        """
        driver = self.browser
        if driver is None:
            raise RuntimeError("Browser session is not initialized.")

        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                element,
            )
            time.sleep(0.2)
            element.click()
        except (ElementClickInterceptedException, StaleElementReferenceException):
            driver.execute_script("arguments[0].click();", element)

    def _get_page_text(self) -> str:
        """
        Returns the visible page text in lowercase for lightweight status checks.

        Returns:
            text (str): normalized page text
        """
        driver = self.browser
        if driver is None:
            raise RuntimeError("Browser session is not initialized.")

        try:
            return driver.find_element(By.TAG_NAME, "body").text.strip().lower()
        except NoSuchElementException:
            return ""

    def _find_visible_button_by_text(self, *labels: str):
        """
        Finds the first visible button whose label matches one of the given texts.

        Args:
            labels (str): candidate button labels

        Returns:
            element: matching Selenium element or None
        """
        driver = self.browser
        if driver is None:
            raise RuntimeError("Browser session is not initialized.")

        normalized_labels = {str(label).strip().lower() for label in labels if str(label).strip()}
        if not normalized_labels:
            return None

        buttons = driver.find_elements(By.XPATH, "//button | //*[@role='button']")
        for button in buttons:
            try:
                if not button.is_displayed():
                    continue
                button_label = " ".join(str(button.text or "").split()).strip().lower()
                if button_label in normalized_labels:
                    return button
            except StaleElementReferenceException:
                continue

        return None

    def _content_checks_modal_is_open(self) -> bool:
        """
        Returns whether YouTube Studio is showing the content-check warning modal.

        Returns:
            is_open (bool): True when the modal is visible
        """
        page_text = self._get_page_text()
        return (
            "we're still checking your content" in page_text
            or "we are still checking your content" in page_text
            or "still checking your content" in page_text
        )

    def _wait_for_content_checks_modal(self, timeout_seconds: int = 8) -> bool:
        """
        Waits briefly to see whether YouTube opens the content-check warning modal.

        Args:
            timeout_seconds (int): maximum wait time

        Returns:
            is_open (bool): True when the modal becomes visible
        """
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._content_checks_modal_is_open():
                return True

            try:
                done_button = self.browser.find_element(By.ID, YOUTUBE_DONE_BUTTON_ID)
                if not done_button.is_displayed():
                    return False
            except (NoSuchElementException, StaleElementReferenceException):
                return False

            time.sleep(1)

        return self._content_checks_modal_is_open()

    def _upload_is_saved_as_private(self, page_text: str | None = None) -> bool:
        """
        Returns whether Studio shows that the uploaded video has been saved.

        Args:
            page_text (str | None): optional cached page text

        Returns:
            is_saved (bool): True when Studio shows "Saved as private"
        """
        normalized_text = str(page_text if page_text is not None else self._get_page_text()).strip().lower()
        return "saved as private" in normalized_text

    def _wait_for_upload_ready(self, timeout_seconds: int = 1800, poll_seconds: float = 5.0) -> None:
        """
        Waits until YouTube Studio confirms the upload has been saved, even if
        the longer content checks are still in progress.

        Args:
            timeout_seconds (int): maximum time to wait
            poll_seconds (float): delay between polls

        Returns:
            None
        """
        driver = self.browser
        if driver is None:
            raise RuntimeError("Browser session is not initialized.")

        verbose = get_verbose()
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            page_text = self._get_page_text()

            if "upload interrupted" in page_text or "resume upload" in page_text:
                raise RuntimeError(
                    "YouTube Studio interrupted the upload and is asking to resume it manually."
                )

            try:
                done_button = driver.find_element(By.ID, YOUTUBE_DONE_BUTTON_ID)
                done_ready = (
                    done_button.is_displayed()
                    and done_button.is_enabled()
                    and str(done_button.get_attribute("aria-disabled") or "").lower() != "true"
                )
            except NoSuchElementException:
                done_ready = False
            except StaleElementReferenceException:
                done_ready = False

            if done_ready and self._upload_is_saved_as_private(page_text):
                return

            if verbose:
                info("\t=> Waiting for YouTube Studio to save the uploaded video before publishing...")
            time.sleep(poll_seconds)

        raise TimeoutException(
            "Timed out while waiting for YouTube Studio to save the uploaded video."
        )

    def upload_video(self) -> bool:
        """
        Uploads the video to YouTube.

        Returns:
            success (bool): Whether the upload was successful or not.
        """
        if self.browser is None:
            raise RuntimeError(
                "YouTube browser session is not initialized. Recreate this account session with browser access enabled."
            )

        try:
            self.get_channel_id()

            driver = self.browser
            verbose = get_verbose()
            wait = WebDriverWait(driver, 30)

            # Go to youtube.com/upload
            driver.get("https://www.youtube.com/upload")

            # Set video file
            FILE_PICKER_TAG = "ytcp-uploads-file-picker"
            file_picker = wait.until(
                EC.presence_of_element_located((By.TAG_NAME, FILE_PICKER_TAG))
            )
            INPUT_TAG = "input"
            file_input = file_picker.find_element(By.TAG_NAME, INPUT_TAG)
            file_input.send_keys(self.video_path)

            # Wait for upload to finish
            time.sleep(5)

            # Set title
            wait.until(lambda drv: len(drv.find_elements(By.ID, YOUTUBE_TEXTBOX_ID)) >= 2)
            textboxes = [
                textbox for textbox in driver.find_elements(By.ID, YOUTUBE_TEXTBOX_ID)
                if textbox.is_displayed()
            ]

            if len(textboxes) < 2:
                raise RuntimeError(
                    f"Could not find the expected YouTube Studio title/description textboxes. Found {len(textboxes)} visible textbox elements."
                )

            title_el = textboxes[0]
            description_el = textboxes[-1]

            if verbose:
                info("\t=> Setting title...")

            self._replace_textbox_value(title_el, self.metadata["title"])

            if verbose:
                info("\t=> Setting description...")

            # Set description
            time.sleep(2)
            self._replace_textbox_value(description_el, self.metadata["description"])

            time.sleep(0.5)

            # Set `made for kids` option
            if verbose:
                info("\t=> Setting `made for kids` option...")

            is_for_kids_checkbox = wait.until(
                EC.presence_of_element_located((By.NAME, YOUTUBE_MADE_FOR_KIDS_NAME))
            )
            is_not_for_kids_checkbox = wait.until(
                EC.presence_of_element_located((By.NAME, YOUTUBE_NOT_MADE_FOR_KIDS_NAME))
            )

            if not self._is_for_kids:
                is_not_for_kids_checkbox.click()
            else:
                is_for_kids_checkbox.click()

            time.sleep(0.5)

            # Click next
            if verbose:
                info("\t=> Clicking next...")

            next_button = wait.until(
                EC.element_to_be_clickable((By.ID, YOUTUBE_NEXT_BUTTON_ID))
            )
            next_button.click()

            # Click next again
            if verbose:
                info("\t=> Clicking next again...")
            next_button = wait.until(
                EC.element_to_be_clickable((By.ID, YOUTUBE_NEXT_BUTTON_ID))
            )
            next_button.click()

            # Wait for 2 seconds
            time.sleep(2)

            # Click next again
            if verbose:
                info("\t=> Clicking next again...")
            next_button = wait.until(
                EC.element_to_be_clickable((By.ID, YOUTUBE_NEXT_BUTTON_ID))
            )
            next_button.click()

            # Set as unlisted
            if verbose:
                info("\t=> Setting as unlisted...")

            wait.until(lambda drv: len(drv.find_elements(By.XPATH, YOUTUBE_RADIO_BUTTON_XPATH)) >= 3)
            radio_button = driver.find_elements(By.XPATH, YOUTUBE_RADIO_BUTTON_XPATH)
            self._click_element(radio_button[2])

            if verbose:
                info("\t=> Waiting for YouTube Studio to show that the upload is saved before the final publish step...")
            self._wait_for_upload_ready()

            if verbose:
                info("\t=> Clicking done button...")

            # Click done button
            done_button = wait.until(
                EC.element_to_be_clickable((By.ID, YOUTUBE_DONE_BUTTON_ID))
            )
            self._click_element(done_button)

            if self._wait_for_content_checks_modal():
                publish_anyway_button = self._find_visible_button_by_text("Publish anyway")
                if publish_anyway_button is not None:
                    if verbose:
                        info("\t=> Content checks warning appeared. Publishing anyway because the upload is already saved.")
                    self._click_element(publish_anyway_button)
                else:
                    raise RuntimeError(
                        "YouTube showed the content-check warning modal, but the 'Publish anyway' button could not be found."
                    )

            # Wait for 2 seconds
            time.sleep(2)

            # Get latest video
            if verbose:
                info("\t=> Getting video URL...")

            # Get the latest uploaded video URL
            driver.get(
                f"https://studio.youtube.com/channel/{self.channel_id}/videos/short"
            )
            time.sleep(2)
            videos = driver.find_elements(By.TAG_NAME, "ytcp-video-row")
            first_video = videos[0]
            anchor_tag = first_video.find_element(By.TAG_NAME, "a")
            href = anchor_tag.get_attribute("href")
            if verbose:
                info(f"\t=> Extracting video ID from URL: {href}")
            video_id = href.split("/")[-2]

            # Build URL
            url = build_url(video_id)

            self.uploaded_video_url = url

            if verbose:
                success(f" => Uploaded Video: {url}")

            # Add video to cache
            try:
                self.add_video(
                    {
                        "title": self.metadata["title"],
                        "description": self.metadata["description"],
                        "hashtags": self.metadata.get("hashtags", []),
                        "tags": self.metadata.get("tags", []),
                        "pricing": self.metadata.get("pricing", {}),
                        "url": url,
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
            except Exception as cache_exc:
                warning(f"Uploaded to YouTube, but failed to update the local video cache: {cache_exc}")

            return True
        except Exception as exc:
            if verbose:
                error(f"YouTube upload failed: {exc}")
            raise RuntimeError(f"YouTube upload failed: {exc}") from exc

    def get_videos(self) -> List[dict]:
        """
        Gets the uploaded videos from the YouTube Channel.

        Returns:
            videos (List[dict]): The uploaded videos.
        """
        if not os.path.exists(get_youtube_cache_path()):
            # Create the cache file
            with open(get_youtube_cache_path(), "w", encoding="utf-8") as file:
                json.dump({"videos": []}, file, indent=4, ensure_ascii=False)
            return []

        videos = []
        # Read the cache file
        with open(get_youtube_cache_path(), "r", encoding="utf-8-sig") as file:
            previous_json = json.load(file)
            # Find our account
            accounts = previous_json["accounts"]
            for account in accounts:
                if account["id"] == self._account_uuid:
                    videos = account["videos"]

        return videos
