import json
import os
import sys
import traceback
from uuid import uuid4
from datetime import datetime
from html import escape

import streamlit as st

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from cache import add_account, get_accounts, remove_account, update_account
from classes.Tts import TTS
from classes.TikTok import TikTok
from classes.Twitter import Twitter
from classes.YouTube import ImageRateLimitError, YouTube
from config import ROOT_DIR as APP_ROOT_DIR
from config import get_configured_llm_model, get_is_for_kids, get_llm_provider, get_pricing_config
from llm_provider import list_models, select_model

CONFIG_PATH = os.path.join(APP_ROOT_DIR, "config.json")
YOUTUBE_DRAFTS_PATH = os.path.join(APP_ROOT_DIR, ".mp", "youtube_drafts.json")


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=2, ensure_ascii=False)


def load_youtube_drafts() -> dict:
    if not os.path.exists(YOUTUBE_DRAFTS_PATH):
        return {"drafts": []}

    try:
        with open(YOUTUBE_DRAFTS_PATH, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return {"drafts": []}


def save_youtube_drafts(payload: dict) -> None:
    with open(YOUTUBE_DRAFTS_PATH, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)


def save_youtube_draft(account_id: str, video_path: str, metadata: dict) -> None:
    drafts_payload = load_youtube_drafts()
    drafts = drafts_payload.get("drafts", [])
    normalized_path = os.path.abspath(video_path)
    normalized_metadata = dict(metadata or {})
    pricing_payload = load_pricing_for_video(normalized_path, normalized_metadata)
    if pricing_payload:
        normalized_metadata["pricing"] = pricing_payload

    drafts = [
        draft for draft in drafts
        if not (
            draft.get("account_id") == account_id
            and os.path.abspath(draft.get("video_path", "")) == normalized_path
        )
    ]
    drafts.append(
        {
            "account_id": account_id,
            "video_path": normalized_path,
            "metadata": normalized_metadata,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )

    drafts_payload["drafts"] = drafts[-20:]
    save_youtube_drafts(drafts_payload)


def get_youtube_drafts_for_account(account_id: str) -> list[dict]:
    drafts_payload = load_youtube_drafts()
    drafts = [
        draft for draft in drafts_payload.get("drafts", [])
        if draft.get("account_id") == account_id and os.path.exists(draft.get("video_path", ""))
    ]
    return sorted(drafts, key=lambda draft: draft.get("created_at", ""), reverse=True)


def get_last_youtube_draft(account_id: str) -> dict | None:
    drafts = get_youtube_drafts_for_account(account_id)
    return drafts[0] if drafts else None


def get_all_youtube_drafts() -> list[dict]:
    drafts_payload = load_youtube_drafts()
    drafts = [
        draft for draft in drafts_payload.get("drafts", [])
        if os.path.exists(draft.get("video_path", ""))
    ]
    return sorted(drafts, key=lambda draft: draft.get("created_at", ""), reverse=True)


def inject_app_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --studio-ink: #12211b;
            --studio-ink-soft: #41584e;
            --studio-accent: #1f7a5a;
            --studio-accent-2: #d0f0d7;
            --studio-warm: #f6f4ee;
            --studio-card: rgba(255, 255, 255, 0.92);
            --studio-border: rgba(18, 33, 27, 0.08);
            --studio-shadow: 0 18px 50px rgba(16, 24, 40, 0.08);
        }
        html, body, [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at top left, rgba(214, 239, 224, 0.6), transparent 32%),
                radial-gradient(circle at top right, rgba(247, 220, 190, 0.45), transparent 24%),
                linear-gradient(180deg, #fbfaf6 0%, #f4f8f4 48%, #f8faf8 100%);
            color: var(--studio-ink);
            font-family: "Segoe UI Variable Text", "Aptos", "Trebuchet MS", sans-serif;
        }
        [data-testid="stHeader"] {
            background: transparent;
        }
        [data-testid="stAppViewContainer"] > .main {
            padding-top: 1.4rem;
        }
        .block-container {
            max-width: 1380px;
            padding-top: 1rem;
            padding-bottom: 4rem;
        }
        [data-testid="stForm"] {
            border: 1px solid var(--studio-border);
            background: rgba(255, 255, 255, 0.78);
            border-radius: 22px;
            padding: 1.1rem 1rem 1.3rem;
            box-shadow: var(--studio-shadow);
        }
        [data-testid="stTabs"] button[role="tab"] {
            border-radius: 999px;
            border: 1px solid rgba(18, 33, 27, 0.08);
            padding: 0.65rem 1rem;
            background: rgba(255, 255, 255, 0.7);
            color: var(--studio-ink-soft);
            font-weight: 600;
        }
        [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
            background: linear-gradient(135deg, #1f7a5a, #2d9d71);
            color: white;
            border-color: transparent;
            box-shadow: 0 10px 24px rgba(31, 122, 90, 0.22);
        }
        [data-testid="stTabs"] {
            margin-top: 1.15rem;
        }
        [data-testid="stMetric"] {
            border: 1px solid var(--studio-border);
            background: var(--studio-card);
            border-radius: 22px;
            padding: 0.55rem 0.9rem;
            box-shadow: var(--studio-shadow);
        }
        [data-testid="stMetricLabel"] {
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }
        [data-testid="stMetricValue"] {
            color: var(--studio-ink);
        }
        .stButton > button, .stDownloadButton > button, [data-testid="baseButton-secondary"] {
            border-radius: 999px;
            min-height: 2.9rem;
            border: 1px solid rgba(18, 33, 27, 0.08);
            font-weight: 700;
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #1f7a5a, #2d9d71);
            color: white;
            border-color: transparent;
        }
        .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] > div,
        .stMultiSelect div[data-baseweb="select"] > div, .stNumberInput input {
            border-radius: 16px !important;
            border-color: rgba(18, 33, 27, 0.12) !important;
            background: rgba(255, 255, 255, 0.92) !important;
        }
        .stTextArea textarea {
            line-height: 1.65;
        }
        .stCheckbox, .stRadio {
            padding-top: 0.2rem;
        }
        [data-testid="stExpander"] {
            border: 1px solid var(--studio-border);
            background: rgba(255, 255, 255, 0.72);
            border-radius: 18px;
            overflow: hidden;
        }
        .hero-shell {
            position: relative;
            overflow: hidden;
            border-radius: 28px;
            padding: 1.65rem 1.8rem;
            margin-bottom: 1rem;
            border: 1px solid rgba(18, 33, 27, 0.08);
            background:
                radial-gradient(circle at right top, rgba(255,255,255,0.42), transparent 22%),
                linear-gradient(135deg, rgba(19, 62, 49, 0.96), rgba(34, 112, 82, 0.94));
            box-shadow: 0 24px 60px rgba(16, 24, 40, 0.14);
            color: #f5fff8;
        }
        .hero-shell::after {
            content: "";
            position: absolute;
            inset: auto -10% -38% auto;
            width: 360px;
            height: 360px;
            background: radial-gradient(circle, rgba(255,255,255,0.18), transparent 62%);
            pointer-events: none;
        }
        .hero-eyebrow {
            font-size: 0.76rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            opacity: 0.78;
        }
        .hero-title {
            font-size: 2rem;
            line-height: 1.1;
            font-weight: 800;
            margin: 0.45rem 0 0.75rem;
        }
        .hero-copy {
            max-width: 780px;
            font-size: 1rem;
            line-height: 1.72;
            color: rgba(245, 255, 248, 0.92);
        }
        .hero-chip-row, .studio-chip-wrap {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .hero-chip {
            display: inline-block;
            padding: 0.38rem 0.72rem;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.14);
            border: 1px solid rgba(255, 255, 255, 0.14);
            color: #f3fff8;
            font-size: 0.82rem;
        }
        .section-intro {
            margin: 0.2rem 0 1rem;
            padding: 1rem 1.1rem 0.6rem;
            border-left: 4px solid rgba(31, 122, 90, 0.28);
        }
        .section-label {
            font-size: 0.74rem;
            letter-spacing: 0.11em;
            text-transform: uppercase;
            font-weight: 700;
            color: #668072;
            margin-bottom: 0.35rem;
        }
        .section-title {
            font-size: 1.45rem;
            line-height: 1.2;
            font-weight: 800;
            color: var(--studio-ink);
            margin: 0 0 0.45rem;
        }
        .section-copy {
            font-size: 0.98rem;
            color: var(--studio-ink-soft);
            line-height: 1.75;
            max-width: 880px;
        }
        .studio-card {
            border: 1px solid var(--studio-border);
            border-radius: 22px;
            padding: 18px 20px;
            background: linear-gradient(180deg, rgba(252, 253, 251, 0.96), rgba(255, 255, 255, 0.98));
            box-shadow: var(--studio-shadow);
            margin-bottom: 14px;
        }
        .studio-eyebrow {
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #668072;
            margin-bottom: 0.4rem;
            font-weight: 700;
        }
        .studio-title {
            font-size: 1.18rem;
            line-height: 1.5;
            font-weight: 700;
            color: var(--studio-ink);
            margin-bottom: 0.55rem;
        }
        .studio-subtitle {
            font-size: 0.92rem;
            color: var(--studio-ink-soft);
            line-height: 1.6;
        }
        .studio-chip {
            display: inline-block;
            padding: 0.32rem 0.68rem;
            border-radius: 999px;
            background: rgba(31, 122, 90, 0.08);
            color: #26463b;
            font-size: 0.85rem;
            border: 1px solid rgba(31, 122, 90, 0.12);
        }
        .studio-chip--muted {
            background: #f8faf8;
            border-color: rgba(102, 128, 114, 0.16);
        }
        .studio-path {
            font-family: Consolas, "Courier New", monospace;
            font-size: 0.9rem;
            line-height: 1.6;
            background: #f8faf8;
            border: 1px solid rgba(102, 128, 114, 0.14);
            border-radius: 18px;
            padding: 0.9rem 1rem;
            color: var(--studio-ink);
            word-break: break-all;
        }
        .studio-kv {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
            margin-top: 0.8rem;
        }
        .studio-kv-item {
            padding: 0.8rem 0.9rem;
            border-radius: 16px;
            background: rgba(244, 248, 244, 0.95);
            border: 1px solid rgba(102, 128, 114, 0.12);
        }
        .studio-kv-label {
            font-size: 0.72rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: #6a8076;
            font-weight: 700;
            margin-bottom: 0.25rem;
        }
        .studio-kv-value {
            color: var(--studio-ink);
            font-size: 0.95rem;
            line-height: 1.5;
            font-weight: 600;
        }
        .studio-note {
            padding: 0.95rem 1rem;
            border-radius: 18px;
            background: rgba(208, 240, 215, 0.44);
            border: 1px solid rgba(31, 122, 90, 0.1);
            color: #224739;
            font-size: 0.95rem;
            line-height: 1.65;
            margin-bottom: 0.9rem;
        }
        .studio-panel {
            border: 1px solid var(--studio-border);
            border-radius: 24px;
            padding: 1rem 1.1rem 1.15rem;
            background: rgba(255, 255, 255, 0.84);
            box-shadow: var(--studio-shadow);
            margin-bottom: 0.9rem;
        }
        .studio-panel--soft {
            background: rgba(249, 251, 248, 0.92);
        }
        .studio-panel-title {
            font-size: 1.05rem;
            font-weight: 800;
            color: var(--studio-ink);
            margin: 0 0 0.8rem;
        }
        .studio-panel-copy {
            font-size: 0.92rem;
            color: var(--studio-ink-soft);
            line-height: 1.65;
            margin-bottom: 0.8rem;
        }
        .studio-inline-actions {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }
        .studio-mini-note {
            font-size: 0.84rem;
            color: #6a8076;
            line-height: 1.55;
        }
        .studio-divider-space {
            height: 0.35rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_section_intro(title: str, body: str, eyebrow: str = "Workspace") -> None:
    st.markdown(
        f"""
        <div class="section-intro">
            <div class="section-label">{escape(eyebrow)}</div>
            <div class="section-title">{escape(title)}</div>
            <div class="section-copy">{escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_hero(title: str, body: str, chips: list[str] | None = None, eyebrow: str = "Studio") -> None:
    chip_markup = ""
    cleaned_chips = [chip.strip() for chip in (chips or []) if str(chip).strip()]
    if cleaned_chips:
        chip_markup = '<div class="hero-chip-row">' + "".join(
            f'<span class="hero-chip">{escape(chip)}</span>' for chip in cleaned_chips
        ) + "</div>"

    st.markdown(
        f"""
        <div class="hero-shell">
            <div class="hero-eyebrow">{escape(eyebrow)}</div>
            <div class="hero-title">{escape(title)}</div>
            <div class="hero-copy">{escape(body)}</div>
            {chip_markup}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kv_card(eyebrow: str, title: str, fields: list[tuple[str, str]]) -> None:
    items = "".join(
        f"""
        <div class="studio-kv-item">
            <div class="studio-kv-label">{escape(label)}</div>
            <div class="studio-kv-value" dir="auto">{escape(value or "Not set")}</div>
        </div>
        """
        for label, value in fields
    )
    st.markdown(
        f"""
        <div class="studio-card">
            <div class="studio-eyebrow">{escape(eyebrow)}</div>
            <div class="studio-title" dir="auto">{escape(title)}</div>
            <div class="studio-kv">{items}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_chip_row(label: str, items: list[str], muted: bool = False) -> None:
    cleaned_items = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned_items:
        return

    chip_class = "studio-chip studio-chip--muted" if muted else "studio-chip"
    chips = "".join(
        f'<span class="{chip_class}">{escape(item)}</span>'
        for item in cleaned_items
    )
    st.markdown(
        f"""
        <div class="studio-card" style="padding: 14px 16px;">
            <div class="studio-eyebrow">{escape(label)}</div>
            <div class="studio-chip-wrap">{chips}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_panel(title: str, body: str | None = None, soft: bool = False) -> None:
    body_markup = (
        f'<div class="studio-panel-copy">{escape(body)}</div>'
        if body and str(body).strip()
        else ""
    )
    classes = "studio-panel studio-panel--soft" if soft else "studio-panel"
    st.markdown(
        f"""
        <div class="{classes}">
            <div class="studio-panel-title">{escape(title)}</div>
            {body_markup}
        </div>
        """,
        unsafe_allow_html=True,
    )


def format_draft_option(draft: dict) -> str:
    title = str(draft.get("metadata", {}).get("title", "(untitled)")).strip() or "(untitled)"
    created_at = str(draft.get("created_at", "")).replace("T", " ")
    basename = os.path.basename(str(draft.get("video_path", "")))
    return f"{created_at} | {title} | {basename}"


def load_pricing_for_video(video_path: str, metadata: dict | None = None) -> dict:
    if isinstance(metadata, dict) and isinstance(metadata.get("pricing"), dict):
        return metadata["pricing"]

    normalized_path = os.path.abspath(str(video_path or "").strip())
    if not normalized_path:
        return {}

    pricing_path = os.path.join(os.path.dirname(normalized_path), "pricing.json")
    if not os.path.exists(pricing_path):
        return {}

    try:
        with open(pricing_path, "r", encoding="utf-8") as file:
            payload = json.load(file)
            return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def render_pricing_report(pricing: dict, key_prefix: str = "pricing") -> None:
    if not pricing:
        st.info("No pricing data is attached to this video yet.")
        return

    currency = str(pricing.get("currency", "USD") or "USD")
    total_cost = float(pricing.get("total_estimated_cost", 0.0) or 0.0)
    items = pricing.get("items", []) if isinstance(pricing.get("items"), list) else []
    summary = pricing.get("summary", {}) if isinstance(pricing.get("summary"), dict) else {}
    notes = pricing.get("notes", []) if isinstance(pricing.get("notes"), list) else []

    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("Estimated Total", f"{total_cost:.4f} {currency}")
    metric2.metric("Tracked Entries", len(items))
    metric3.metric("Billable Categories", len([item for item in summary.values() if float(item.get("estimated_cost", 0.0) or 0.0) > 0]))

    if summary:
        summary_rows = []
        for category, values in summary.items():
            summary_rows.append(
                {
                    "Category": category.replace("_", " ").title(),
                    "Estimated Cost": f"{float(values.get('estimated_cost', 0.0) or 0.0):.4f} {currency}",
                    "Entries": int(values.get("entries", 0) or 0),
                    "Quantity": float(values.get("quantity", 0.0) or 0.0),
                    "Units": ", ".join(values.get("units", [])),
                }
            )
        st.markdown("#### Category Summary")
        st.dataframe(summary_rows, use_container_width=True, hide_index=True, key=f"{key_prefix}_summary")

    if items:
        item_rows = []
        for item in items:
            item_rows.append(
                {
                    "Category": str(item.get("category", "")).replace("_", " ").title(),
                    "Type": item.get("type", ""),
                    "Provider": item.get("provider", ""),
                    "Model": item.get("model", ""),
                    "Quantity": item.get("quantity", 0),
                    "Unit": item.get("unit", ""),
                    "Estimated Cost": f"{float(item.get('estimated_cost', 0.0) or 0.0):.4f} {currency}",
                }
            )
        st.markdown("#### Line Items")
        st.dataframe(item_rows, use_container_width=True, hide_index=True, key=f"{key_prefix}_items")

    if notes:
        st.markdown("#### Notes")
        for note in notes:
            st.markdown(
                f"""
                <div class="studio-note" style="margin-bottom: 0.6rem;">
                    {escape(str(note))}
                </div>
                """,
                unsafe_allow_html=True,
            )


def ensure_llm_selected() -> tuple[str, str]:
    provider = get_llm_provider()
    model = get_configured_llm_model()

    if provider == "openai":
        model = model or "gpt-5-mini"
        select_model(model, provider=provider)
        return provider, model

    if not model:
        models = list_models(provider=provider)
        if not models:
            raise RuntimeError(
                "No Ollama models are available. Set ollama_model in config.json or pull a model first."
            )
        model = models[0]

    select_model(model, provider=provider)
    return provider, model


def get_youtube_metadata_model_options(config: dict) -> tuple[list[str], str, str | None]:
    provider = str(config.get("llm_provider", "ollama") or "ollama").strip().lower()
    if provider not in ("openai", "ollama"):
        provider = "ollama"

    active_model = str(
        config.get("openai_model", "") if provider == "openai" else config.get("ollama_model", "")
    ).strip()
    configured_model = str(config.get("youtube_metadata_model", "") or "").strip()

    discovered_models: list[str] = []
    warning_message = None
    try:
        discovered_models = list_models(provider=provider)
    except Exception as exc:
        warning_message = (
            f"Couldn't load live {provider.title()} models right now, so the metadata list is using saved values only: {exc}"
        )

    option_candidates: list[str] = []
    if discovered_models:
        if configured_model and configured_model in discovered_models:
            option_candidates.append(configured_model)
        if active_model:
            option_candidates.append(active_model)
        option_candidates.extend(discovered_models)
    else:
        if configured_model:
            option_candidates.append(configured_model)
        if active_model:
            option_candidates.append(active_model)

    options = [""]
    seen = {""}
    for model_name in option_candidates:
        if model_name and model_name not in seen:
            options.append(model_name)
            seen.add(model_name)

    if provider == "openai":
        auto_label = "Auto (gpt-5-nano)"
    elif active_model:
        auto_label = f"Auto ({active_model})"
    else:
        auto_label = "Auto (use the active Ollama model)"
    return options, auto_label, warning_message


def render_overview() -> None:
    config = load_config()
    provider = config.get("llm_provider", "ollama")
    active_model = config.get("openai_model") if provider == "openai" else config.get("ollama_model")
    render_hero(
        "MoneyPrinter V2 Studio",
        "Manage accounts, generate short-form content, review drafts, and publish across Twitter, YouTube, and TikTok from one calmer workspace.",
        chips=[
            f"LLM: {provider}",
            f"Model: {active_model or 'Not set'}",
            f"Image provider: {config.get('image_provider', 'nanobanana2')}",
            f"TTS: {config.get('tts_provider', 'auto')}",
        ],
        eyebrow="Control Room",
    )

    twitter_accounts = get_accounts("twitter")
    youtube_accounts = get_accounts("youtube")
    tiktok_accounts = get_accounts("tiktok")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Twitter Accounts", len(twitter_accounts))
    col2.metric("YouTube Accounts", len(youtube_accounts))
    col3.metric("TikTok Accounts", len(tiktok_accounts))
    col4.metric("LLM Provider", provider)
    col5.metric("Active Model", active_model or "Not set")

    note_col, guide_col = st.columns([1.2, 1])
    with note_col:
        st.markdown(
            """
            <div class="studio-note">
                Close Firefox before using browser automation actions like <strong>Post</strong> or <strong>Upload</strong>.
                Preview, generation, and metadata work can all be done without launching a browser first.
            </div>
            """,
            unsafe_allow_html=True,
        )
    with guide_col:
        render_kv_card(
            "Quick Start",
            "Best flow for smooth runs",
            [
                ("1", "Set providers and defaults in Config"),
                ("2", "Create or update your channel/account profiles"),
                ("3", "Generate previews before posting or uploading"),
                ("4", "Keep Firefox closed until the final browser action"),
            ],
        )


def render_config_tab() -> None:
    render_section_intro(
        "Configuration",
        "Set the core providers and defaults here. Keep expensive providers only where quality matters, and use the lighter options for metadata or retries.",
        eyebrow="Settings",
    )
    config = load_config()
    pricing_defaults = get_pricing_config()
    if not isinstance(config.get("pricing"), dict):
        config["pricing"] = pricing_defaults

    def as_int(value: object, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def as_float(value: object, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    current_target_duration = max(0, as_int(config.get("youtube_target_duration_seconds", 30), 30))
    current_min_prompts = max(1, as_int(config.get("min_image_prompts", 10), 10))
    current_max_prompts = max(current_min_prompts, as_int(config.get("max_image_prompts", 12), 12))

    top_summary_col, top_note_col = st.columns([1.15, 0.85])
    with top_summary_col:
        render_kv_card(
            "Current Defaults",
            "Global automation profile",
            [
                ("LLM provider", str(config.get("llm_provider", "ollama"))),
                ("Image provider", str(config.get("image_provider", "nanobanana2"))),
                ("Asset strategy", str(config.get("asset_strategy", "mixed"))),
                ("TTS provider", str(config.get("tts_provider", "auto"))),
                ("Target duration", f"{config.get('youtube_target_duration_seconds', 30)}s"),
            ],
        )
    with top_note_col:
        st.markdown(
            """
            <div class="studio-note">
                Keep this page for defaults and provider setup only. Channel voice, dialect, and publishing choices are usually better handled in the account and studio tabs.
            </div>
            """,
            unsafe_allow_html=True,
        )

    with st.form("config_form"):
        core_tab, providers_tab, video_tab, pricing_tab, advanced_tab = st.tabs(
            ["Core Setup", "Providers + Keys", "Voice + Video", "Pricing", "Advanced"]
        )

        with core_tab:
            left, right = st.columns(2)
            with left:
                render_panel("Browser + Core", "The defaults used when an account does not override them.", soft=True)
                config["firefox_profile"] = st.text_input(
                    "Default Firefox profile",
                    value=config.get("firefox_profile", ""),
                    help="Fallback profile path used when a specific account does not define one.",
                )
                config["headless"] = st.checkbox("Headless browser", value=config.get("headless", False))
                config["verbose"] = st.checkbox("Verbose logs", value=config.get("verbose", True))
                config["llm_provider"] = st.selectbox(
                    "LLM provider",
                    options=["openai", "ollama"],
                    index=0 if config.get("llm_provider", "ollama") == "openai" else 1,
                )
                config["openai_model"] = st.text_input(
                    "OpenAI model",
                    value=config.get("openai_model", "gpt-5-mini"),
                )
                config["ollama_model"] = st.text_input(
                    "Ollama model",
                    value=config.get("ollama_model", ""),
                )
            with right:
                render_panel("Language Defaults", "These values mainly shape social content defaults and narration guidance.", soft=True)
                config["twitter_language"] = st.text_input(
                    "Twitter language",
                    value=config.get("twitter_language", "English"),
                )
                config["twitter_dialect"] = st.text_input(
                    "Twitter dialect",
                    value=config.get("twitter_dialect", ""),
                )
                metadata_model_options, metadata_auto_label, metadata_model_warning = get_youtube_metadata_model_options(config)
                current_metadata_model = str(config.get("youtube_metadata_model", "") or "").strip()
                metadata_model_index = (
                    metadata_model_options.index(current_metadata_model)
                    if current_metadata_model in metadata_model_options
                    else 0
                )
                config["youtube_metadata_model"] = st.selectbox(
                    "YouTube metadata model",
                    options=metadata_model_options,
                    index=metadata_model_index,
                    format_func=lambda model_name: metadata_auto_label if model_name == "" else model_name,
                    help="Optional lighter model override for YouTube title/description/tags. Choices follow the selected LLM provider.",
                )
                st.caption(
                    "This list follows the selected LLM provider. If you change the provider or base model above, refresh the metadata choices before saving."
                )
                if metadata_model_warning:
                    st.caption(metadata_model_warning)

        with providers_tab:
            left, right = st.columns(2)
            with left:
                render_panel("Image Providers", "Choose the main image path and the stock-vs-AI strategy.", soft=True)
                config["image_provider"] = st.selectbox(
                    "Image provider",
                    options=["openai", "openrouter", "nanobanana2"],
                    index=["openai", "openrouter", "nanobanana2"].index(
                        config.get("image_provider", "nanobanana2")
                        if config.get("image_provider", "nanobanana2") in ["openai", "openrouter", "nanobanana2"]
                        else "nanobanana2"
                    ),
                )
                config["asset_strategy"] = st.selectbox(
                    "Visual asset strategy",
                    options=["mixed", "pixabay_only", "ai_only"],
                    index=["mixed", "pixabay_only", "ai_only"].index(
                        config.get("asset_strategy", "mixed")
                        if config.get("asset_strategy", "mixed") in ["mixed", "pixabay_only", "ai_only"]
                        else "mixed"
                    ),
                    help="Use free Pixabay stock first, AI only, or a mixed cost-saving approach.",
                )
                config["max_ai_assets"] = int(
                    st.number_input(
                        "Max AI visual assets",
                        min_value=0,
                        max_value=12,
                        step=1,
                        value=max(0, as_int(config.get("max_ai_assets", 2), 2)),
                        help="Used by the mixed strategy to limit how many AI-generated images are allowed per video.",
                    )
                )
                config["openai_image_model"] = st.text_input(
                    "OpenAI image model",
                    value=config.get("openai_image_model", "gpt-image-1"),
                )
                config["openrouter_image_model"] = st.text_input(
                    "OpenRouter image model",
                    value=config.get("openrouter_image_model", "black-forest-labs/flux.2-flex"),
                )
                config["nanobanana2_model"] = st.text_input(
                    "Nano Banana model",
                    value=config.get("nanobanana2_model", "gemini-3.1-flash-image-preview"),
                )
                config["openai_image_quality"] = st.selectbox(
                    "OpenAI image quality",
                    options=["low", "medium", "high"],
                    index=["low", "medium", "high"].index(
                        config.get("openai_image_quality", "low")
                        if config.get("openai_image_quality", "low") in ["low", "medium", "high"]
                        else "low"
                    ),
                )
            with right:
                render_panel("Keys + Endpoints", "Sensitive keys stay grouped here so the rest of the page stays lighter to scan.", soft=True)
                config["openai_base_url"] = st.text_input(
                    "OpenAI base URL",
                    value=config.get("openai_base_url", "https://api.openai.com/v1"),
                )
                config["ollama_base_url"] = st.text_input(
                    "Ollama base URL",
                    value=config.get("ollama_base_url", "http://127.0.0.1:11434"),
                )
                config["openai_api_key"] = st.text_input(
                    "OpenAI API key",
                    value=config.get("openai_api_key", ""),
                    type="password",
                )
                config["openrouter_api_key"] = st.text_input(
                    "OpenRouter API key",
                    value=config.get("openrouter_api_key", ""),
                    type="password",
                )
                config["pixabay_api_key"] = st.text_input(
                    "Pixabay API key",
                    value=config.get("pixabay_api_key", ""),
                    type="password",
                )

        with video_tab:
            left, right = st.columns(2)
            with left:
                render_panel("Narration", "Set the speech provider and the subtitle look that will be burned into videos.", soft=True)
                config["tts_provider"] = st.selectbox(
                    "TTS provider",
                    options=["auto", "openai", "kitten"],
                    index=["auto", "openai", "kitten"].index(
                        config.get("tts_provider", "auto")
                        if config.get("tts_provider", "auto") in ["auto", "openai", "kitten"]
                        else "auto"
                    ),
                )
                config["openai_tts_model"] = st.text_input(
                    "OpenAI TTS model",
                    value=config.get("openai_tts_model", "gpt-4o-mini-tts"),
                )
                config["openai_tts_voice"] = st.text_input(
                    "OpenAI TTS voice",
                    value=config.get("openai_tts_voice", "onyx"),
                )
                subtitle_mode_options = ["word_by_word", "chunk"]
                current_subtitle_mode = config.get("subtitle_mode", "word_by_word")
                if current_subtitle_mode not in subtitle_mode_options:
                    current_subtitle_mode = "word_by_word"
                config["subtitle_mode"] = st.selectbox(
                    "Subtitle timing mode",
                    options=subtitle_mode_options,
                    index=subtitle_mode_options.index(current_subtitle_mode),
                    help="word_by_word shows one word at a time. chunk shows short grouped phrases.",
                )
                config["subtitle_font_english"] = st.text_input(
                    "English subtitle font file",
                    value=config.get("subtitle_font_english", config.get("subtitle_font", config.get("font", "bold_font.ttf"))),
                )
                config["subtitle_font_arabic"] = st.text_input(
                    "Arabic subtitle font file",
                    value=config.get("subtitle_font_arabic", config.get("subtitle_font", config.get("font", "bold_font.ttf"))),
                )
                config["subtitle_font_size"] = int(
                    st.number_input(
                        "Subtitle font size",
                        min_value=24,
                        max_value=180,
                        step=2,
                        value=max(24, as_int(config.get("subtitle_font_size", 84), 84)),
                    )
                )
                config["subtitle_color"] = st.text_input(
                    "Subtitle color",
                    value=config.get("subtitle_color", "#FFF7D6"),
                )
                config["subtitle_stroke_color"] = st.text_input(
                    "Subtitle stroke color",
                    value=config.get("subtitle_stroke_color", "#000000"),
                )
                config["subtitle_stroke_width"] = int(
                    st.number_input(
                        "Subtitle stroke width",
                        min_value=0,
                        max_value=20,
                        step=1,
                        value=max(0, as_int(config.get("subtitle_stroke_width", 6), 6)),
                    )
                )
            with right:
                render_panel("Video Rhythm", "These defaults mostly affect pacing, asset count, and search breadth.", soft=True)
                config["youtube_target_duration_seconds"] = int(
                    st.number_input(
                        "Target video duration (seconds)",
                        min_value=0,
                        max_value=300,
                        step=5,
                        value=current_target_duration,
                        help="Guides script generation toward a target spoken runtime. Set 0 to disable.",
                    )
                )
                config["min_image_prompts"] = int(
                    st.number_input(
                        "Min image prompts",
                        min_value=1,
                        max_value=30,
                        step=1,
                        value=current_min_prompts,
                    )
                )
                config["max_image_prompts"] = int(
                    st.number_input(
                        "Max image prompts",
                        min_value=config["min_image_prompts"],
                        max_value=40,
                        step=1,
                        value=max(config["min_image_prompts"], current_max_prompts),
                    )
                )
                config["pixabay_results_per_query"] = int(
                    st.number_input(
                        "Pixabay results per query",
                        min_value=3,
                        max_value=20,
                        step=1,
                        value=min(20, max(3, as_int(config.get("pixabay_results_per_query", 6), 6))),
                    )
                )
                config["subtitle_font"] = st.text_input(
                    "Legacy subtitle font file",
                    value=config.get("subtitle_font", config.get("font", "bold_font.ttf")),
                    help="Fallback font file kept for backward compatibility.",
                )

        with pricing_tab:
            pricing = config.setdefault("pricing", {})
            pricing.setdefault("currency", pricing_defaults.get("currency", "USD"))
            text_generation = pricing.setdefault("text_generation", {})
            openai_text = text_generation.setdefault(
                "openai",
                pricing_defaults.get("text_generation", {}).get("openai", {}),
            )
            ollama_text = text_generation.setdefault(
                "ollama",
                pricing_defaults.get("text_generation", {}).get("ollama", {}),
            )
            openai_text_models = openai_text.setdefault("models", {})
            ollama_text_models = ollama_text.setdefault("models", {})
            gpt5mini_pricing = openai_text_models.setdefault(
                "gpt-5-mini",
                pricing_defaults["text_generation"]["openai"]["models"]["gpt-5-mini"],
            )
            gpt5nano_pricing = openai_text_models.setdefault(
                "gpt-5-nano",
                pricing_defaults["text_generation"]["openai"]["models"]["gpt-5-nano"],
            )
            ollama_default_pricing = ollama_text_models.setdefault(
                "default",
                pricing_defaults["text_generation"]["ollama"]["models"]["default"],
            )

            image_generation = pricing.setdefault("image_generation", {})
            openai_images = image_generation.setdefault(
                "openai",
                pricing_defaults["image_generation"]["openai"],
            )
            nanobanana_images = image_generation.setdefault(
                "nanobanana2",
                pricing_defaults["image_generation"]["nanobanana2"],
            )
            openrouter_images = image_generation.setdefault(
                "openrouter",
                pricing_defaults["image_generation"]["openrouter"],
            )
            pixabay_images = image_generation.setdefault(
                "pixabay",
                pricing_defaults["image_generation"]["pixabay"],
            )
            openai_image_model_pricing = openai_images.setdefault("models", {}).setdefault(
                "gpt-image-1",
                pricing_defaults["image_generation"]["openai"]["models"]["gpt-image-1"],
            )
            openai_image_qualities = openai_image_model_pricing.setdefault(
                "qualities",
                pricing_defaults["image_generation"]["openai"]["models"]["gpt-image-1"]["qualities"],
            )
            nanobanana_default_pricing = nanobanana_images.setdefault("models", {}).setdefault(
                "gemini-3.1-flash-image-preview",
                pricing_defaults["image_generation"]["nanobanana2"]["models"]["gemini-3.1-flash-image-preview"],
            )
            openrouter_default_pricing = openrouter_images.setdefault("models", {}).setdefault(
                "default",
                pricing_defaults["image_generation"]["openrouter"]["models"]["default"],
            )
            pixabay_default_pricing = pixabay_images.setdefault("models", {}).setdefault(
                "default",
                pricing_defaults["image_generation"]["pixabay"]["models"]["default"],
            )

            tts_pricing = pricing.setdefault("tts", {})
            openai_tts = tts_pricing.setdefault("openai", pricing_defaults["tts"]["openai"])
            kitten_tts = tts_pricing.setdefault("kitten", pricing_defaults["tts"]["kitten"])
            openai_tts_default = openai_tts.setdefault("models", {}).setdefault(
                "gpt-4o-mini-tts",
                pricing_defaults["tts"]["openai"]["models"]["gpt-4o-mini-tts"],
            )
            kitten_tts_default = kitten_tts.setdefault("models", {}).setdefault(
                "default",
                pricing_defaults["tts"]["kitten"]["models"]["default"],
            )

            stt_pricing = pricing.setdefault("stt", {})
            script_based_stt = stt_pricing.setdefault(
                "script_based",
                pricing_defaults["stt"]["script_based"],
            )
            whisper_stt = stt_pricing.setdefault(
                "local_whisper",
                pricing_defaults["stt"]["local_whisper"],
            )
            assemblyai_stt = stt_pricing.setdefault(
                "third_party_assemblyai",
                pricing_defaults["stt"]["third_party_assemblyai"],
            )
            script_based_default = script_based_stt.setdefault("models", {}).setdefault(
                "default",
                pricing_defaults["stt"]["script_based"]["models"]["default"],
            )
            whisper_default = whisper_stt.setdefault("models", {}).setdefault(
                "default",
                pricing_defaults["stt"]["local_whisper"]["models"]["default"],
            )
            assemblyai_default = assemblyai_stt.setdefault("models", {}).setdefault(
                "default",
                pricing_defaults["stt"]["third_party_assemblyai"]["models"]["default"],
            )

            left, right = st.columns(2)
            with left:
                render_panel(
                    "Tracked Rates",
                    "These values power the per-video cost estimate. Adjust them if your provider pricing or plan differs.",
                    soft=True,
                )
                pricing["currency"] = st.text_input(
                    "Pricing currency",
                    value=str(pricing.get("currency", pricing_defaults.get("currency", "USD"))),
                )
                st.markdown("##### Text Generation")
                gpt5mini_pricing["input_per_1m_tokens"] = as_float(
                    st.text_input(
                        "OpenAI gpt-5-mini input / 1M tokens",
                        value=str(gpt5mini_pricing.get("input_per_1m_tokens", 0.25)),
                    ),
                    0.25,
                )
                gpt5mini_pricing["output_per_1m_tokens"] = as_float(
                    st.text_input(
                        "OpenAI gpt-5-mini output / 1M tokens",
                        value=str(gpt5mini_pricing.get("output_per_1m_tokens", 2.0)),
                    ),
                    2.0,
                )
                gpt5nano_pricing["input_per_1m_tokens"] = as_float(
                    st.text_input(
                        "OpenAI gpt-5-nano input / 1M tokens",
                        value=str(gpt5nano_pricing.get("input_per_1m_tokens", 0.05)),
                    ),
                    0.05,
                )
                gpt5nano_pricing["output_per_1m_tokens"] = as_float(
                    st.text_input(
                        "OpenAI gpt-5-nano output / 1M tokens",
                        value=str(gpt5nano_pricing.get("output_per_1m_tokens", 0.4)),
                    ),
                    0.4,
                )
                ollama_default_pricing["input_per_1m_tokens"] = as_float(
                    st.text_input(
                        "Ollama default input / 1M tokens",
                        value=str(ollama_default_pricing.get("input_per_1m_tokens", 0.0)),
                        help="Leave at 0 if your Ollama usage is effectively flat-cost or you do not want to estimate it.",
                    ),
                    0.0,
                )
                ollama_default_pricing["output_per_1m_tokens"] = as_float(
                    st.text_input(
                        "Ollama default output / 1M tokens",
                        value=str(ollama_default_pricing.get("output_per_1m_tokens", 0.0)),
                    ),
                    0.0,
                )

                st.markdown("##### Speech + Subtitles")
                openai_tts_default["per_minute_audio"] = as_float(
                    st.text_input(
                        "OpenAI TTS cost / minute audio",
                        value=str(openai_tts_default.get("per_minute_audio", 0.015)),
                    ),
                    0.015,
                )
                kitten_tts_default["per_minute_audio"] = as_float(
                    st.text_input(
                        "Kitten TTS cost / minute audio",
                        value=str(kitten_tts_default.get("per_minute_audio", 0.0)),
                    ),
                    0.0,
                )
                script_based_default["per_minute_audio"] = as_float(
                    st.text_input(
                        "Script-based subtitles cost / minute audio",
                        value=str(script_based_default.get("per_minute_audio", 0.0)),
                    ),
                    0.0,
                )
                whisper_default["per_minute_audio"] = as_float(
                    st.text_input(
                        "Local Whisper cost / minute audio",
                        value=str(whisper_default.get("per_minute_audio", 0.0)),
                    ),
                    0.0,
                )
                assemblyai_default["per_minute_audio"] = as_float(
                    st.text_input(
                        "AssemblyAI cost / minute audio",
                        value=str(assemblyai_default.get("per_minute_audio", 0.0025)),
                    ),
                    0.0025,
                )

            with right:
                render_panel(
                    "Image Costs",
                    "AI image pricing is tracked per successful generated image. Pixabay defaults to free unless you want to assign an internal media cost.",
                    soft=True,
                )
                openai_image_qualities["low"] = as_float(
                    st.text_input(
                        "OpenAI image low quality / image",
                        value=str(openai_image_qualities.get("low", 0.016)),
                    ),
                    0.016,
                )
                openai_image_qualities["medium"] = as_float(
                    st.text_input(
                        "OpenAI image medium quality / image",
                        value=str(openai_image_qualities.get("medium", 0.063)),
                    ),
                    0.063,
                )
                openai_image_qualities["high"] = as_float(
                    st.text_input(
                        "OpenAI image high quality / image",
                        value=str(openai_image_qualities.get("high", 0.25)),
                    ),
                    0.25,
                )
                nanobanana_default_pricing["per_image"] = as_float(
                    st.text_input(
                        "Nano Banana cost / image",
                        value=str(nanobanana_default_pricing.get("per_image", 0.039)),
                    ),
                    0.039,
                )
                openrouter_default_pricing["per_image"] = as_float(
                    st.text_input(
                        "OpenRouter image cost / image",
                        value=str(openrouter_default_pricing.get("per_image", 0.0)),
                    ),
                    0.0,
                )
                pixabay_default_pricing["per_asset"] = as_float(
                    st.text_input(
                        "Pixabay stock asset cost / asset",
                        value=str(pixabay_default_pricing.get("per_asset", 0.0)),
                    ),
                    0.0,
                )
                st.markdown(
                    """
                    <div class="studio-note">
                        Tip: If you use an Ollama cloud model or any provider plan with custom pricing, set those numbers here so the report reflects your real spend more closely.
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        with advanced_tab:
            st.markdown(
                """
                <div class="studio-note">
                    Advanced values stay here so the main setup path stays short. Most day-to-day work should only need the first three groups.
                </div>
                """,
                unsafe_allow_html=True,
            )
            with st.expander("Raw config JSON", expanded=False):
                st.code(json.dumps(config, ensure_ascii=False, indent=2), language="json")

        refresh_col, save_col = st.columns(2)
        with refresh_col:
            refresh_metadata_choices = st.form_submit_button(
                "Refresh Metadata Choices",
                use_container_width=True,
            )
        with save_col:
            submitted = st.form_submit_button("Save Config", use_container_width=True)

    if refresh_metadata_choices:
        st.info("Metadata model choices were refreshed for the selected LLM provider and base model.")
    if submitted:
        save_config(config)
        st.success("Config saved.")


def render_account_editor(provider: str) -> None:
    provider_title = provider.title()
    render_section_intro(
        f"{provider_title} Accounts",
        "Keep each account’s voice, profile path, and defaults explicit so generation stays consistent over time.",
        eyebrow="Accounts",
    )
    accounts = get_accounts(provider)

    if accounts:
        selected_id = st.selectbox(
            f"Select {provider} account",
            options=[account["id"] for account in accounts],
            format_func=lambda account_id: next(
                f'{account["nickname"]} ({account["id"]})'
                for account in accounts
                if account["id"] == account_id
            ),
            key=f"{provider}_account_select",
        )
        selected_account = next(account for account in accounts if account["id"] == selected_id)

        summary_fields = [
            ("Nickname", str(selected_account.get("nickname", ""))),
            ("Profile", os.path.basename(str(selected_account.get("firefox_profile", ""))) or "Not set"),
        ]
        if provider == "twitter":
            summary_fields.extend(
                [
                    ("Topic", str(selected_account.get("topic", ""))),
                    ("Posts", str(len(selected_account.get("posts", [])))),
                ]
            )
        else:
            summary_fields.extend(
                [
                    ("Language", str(selected_account.get("language", "English"))),
                    ("Dialect", str(selected_account.get("dialect", "") or "Default")),
                    ("Videos", str(len(selected_account.get("videos", [])))),
                ]
            )
            if provider == "youtube":
                summary_fields.append(
                    ("Made for kids", "Yes" if selected_account.get("is_for_kids", get_is_for_kids()) else "No")
                )

        render_kv_card("Selected Account", selected_account.get("nickname", provider_title), summary_fields)

        with st.form(f"edit_{provider}_account"):
            st.markdown("##### Identity")
            nickname = st.text_input("Nickname", value=selected_account.get("nickname", ""))
            firefox_profile = st.text_input(
                "Firefox profile",
                value=selected_account.get("firefox_profile", ""),
            )
            character_context = st.text_area(
                "Character context",
                value=selected_account.get("character_context", ""),
                help="Describe the persona, tone, audience, recurring themes, and what this account should sound like.",
            )

            if provider == "twitter":
                st.markdown("##### Posting Defaults")
                topic = st.text_input("Topic", value=selected_account.get("topic", ""))
            else:
                st.markdown("##### Channel Defaults")
                niche = st.text_input("Niche", value=selected_account.get("niche", ""))
                language = st.text_input("Language", value=selected_account.get("language", "English"))
                dialect = st.text_input("Dialect", value=selected_account.get("dialect", ""))
                is_for_kids = (
                    st.checkbox("Made for kids by default", value=selected_account.get("is_for_kids", get_is_for_kids()))
                    if provider == "youtube"
                    else False
                )

            save_button = st.form_submit_button("Save Changes", use_container_width=True)

        if save_button:
            updates = {
                "nickname": nickname,
                "firefox_profile": firefox_profile,
                "character_context": character_context,
            }
            if provider == "twitter":
                updates["topic"] = topic
            else:
                updates["niche"] = niche
                updates["language"] = language
                updates["dialect"] = dialect
                if provider == "youtube":
                    updates["is_for_kids"] = is_for_kids

            update_account(provider, selected_id, updates)
            st.success("Account updated.")
            st.rerun()

        if st.button(
            f"Delete {provider.title()} Account",
            type="secondary",
            use_container_width=True,
            key=f"delete_{provider}_account",
        ):
            remove_account(provider, selected_id)
            st.warning("Account deleted.")
            st.rerun()

        with st.expander("Selected account JSON"):
            st.code(json.dumps(selected_account, ensure_ascii=False, indent=2), language="json")

    else:
        st.info(f"No {provider} accounts yet.")

    st.markdown("---")
    st.markdown(f"#### Create {provider_title} Account")

    with st.form(f"create_{provider}_account"):
        st.markdown("##### New account basics")
        nickname = st.text_input("Nickname", key=f"create_{provider}_nickname")
        firefox_profile = st.text_input("Firefox profile", key=f"create_{provider}_profile")
        character_context = st.text_area(
            "Character context",
            key=f"create_{provider}_character_context",
            help="Example: Smart, casual Egyptian creator who gives sharp opinions about trending TV drama and always sounds natural, witty, and current.",
        )

        if provider == "twitter":
            topic = st.text_input("Topic", key="create_twitter_topic")
        else:
            niche = st.text_input("Niche", key=f"create_{provider}_niche")
            language = st.text_input("Language", value="English", key=f"create_{provider}_language")
            dialect = st.text_input("Dialect", value="", key=f"create_{provider}_dialect")
            create_is_for_kids = (
                st.checkbox("Made for kids by default", value=False, key=f"create_{provider}_is_for_kids")
                if provider == "youtube"
                else False
            )

        create_button = st.form_submit_button(f"Create {provider.title()} Account", use_container_width=True)

    if create_button:
        account = {
            "id": str(uuid4()),
            "nickname": nickname,
            "firefox_profile": firefox_profile,
            "character_context": character_context,
        }
        if provider == "twitter":
            account["topic"] = topic
            account["posts"] = []
        else:
            account["niche"] = niche
            account["language"] = language
            account["dialect"] = dialect
            if provider == "youtube":
                account["is_for_kids"] = create_is_for_kids
            account["videos"] = []

        add_account(provider, account)
        st.success(f"{provider.title()} account created.")
        st.rerun()


def render_twitter_studio() -> None:
    render_section_intro(
        "Twitter Studio",
        "Generate a single post, refine it if needed, and publish only when the tone feels right for that account.",
        eyebrow="Publishing",
    )
    accounts = get_accounts("twitter")
    if not accounts:
        st.info("Create a Twitter account first.")
        return

    account_id = st.selectbox(
        "Twitter account",
        options=[account["id"] for account in accounts],
        format_func=lambda selected_id: next(
            f'{account["nickname"]} - {account.get("topic", "")}'
            for account in accounts
            if account["id"] == selected_id
        ),
    )
    account = next(account for account in accounts if account["id"] == account_id)

    topic_override = st.text_input("Topic override", value=account.get("topic", ""))
    context_override = st.text_area(
        "Character context override",
        value=account.get("character_context", ""),
    )

    render_kv_card(
        "Active Account",
        account.get("nickname", "Twitter account"),
        [
            ("Topic", topic_override or "Not set"),
            ("Language", str(load_config().get("twitter_language", "English"))),
            ("Dialect", str(load_config().get("twitter_dialect", "") or "Default")),
            ("Cached posts", str(len(account.get("posts", [])))),
        ],
    )

    col1, col2 = st.columns(2)

    if col1.button("Generate Tweet Preview", use_container_width=True):
        try:
            provider, model = ensure_llm_selected()
            twitter = Twitter(
                account["id"],
                account["nickname"],
                account["firefox_profile"],
                topic_override,
                context_override,
                open_browser=False,
            )
            st.session_state["twitter_preview_text"] = twitter.generate_post()
            st.session_state["twitter_preview_account_id"] = account["id"]
            st.success(f"Preview generated with {provider}:{model}")
        except Exception as exc:
            st.error(str(exc))
            st.code(traceback.format_exc())

    if col2.button("Post Preview to X", use_container_width=True):
        try:
            preview_text = st.session_state.get("twitter_preview_text", "").strip()
            if not preview_text:
                raise RuntimeError("Generate or edit a preview first.")

            twitter = Twitter(
                account["id"],
                account["nickname"],
                account["firefox_profile"],
                topic_override,
                context_override,
                open_browser=True,
            )
            twitter.post(preview_text)
            st.success("Tweet posted.")
        except Exception as exc:
            st.error(str(exc))
            st.code(traceback.format_exc())

    preview_text = st.text_area(
        "Tweet preview",
        value=st.session_state.get("twitter_preview_text", ""),
        height=160,
    )
    st.session_state["twitter_preview_text"] = preview_text

    recent_posts = account.get("posts", [])
    if recent_posts:
        with st.expander("Recent cached posts"):
            for post in reversed(recent_posts[-10:]):
                st.write(f'[{post.get("date", "")}] {post.get("content", "")}')


def render_youtube_studio() -> None:
    render_section_intro(
        "YouTube Studio",
        "Preview topics and scripts, generate full vertical videos, and manage a reusable draft library before upload.",
        eyebrow="Video Workflow",
    )
    accounts = get_accounts("youtube")
    if not accounts:
        st.info("Create a YouTube account first.")
        return

    account_id = st.selectbox(
        "YouTube account",
        options=[account["id"] for account in accounts],
        format_func=lambda selected_id: next(
            f'{account["nickname"]} - {account.get("niche", "")}'
            for account in accounts
            if account["id"] == selected_id
        ),
    )
    account = next(account for account in accounts if account["id"] == account_id)

    drafts = get_youtube_drafts_for_account(account["id"])
    preview = st.session_state.get("youtube_preview", {})
    generated = st.session_state.get("youtube_generated_video", {})
    if generated.get("account_id") == account["id"] and generated.get("video_path"):
        generated_path = os.path.abspath(generated.get("video_path", ""))
        if not any(os.path.abspath(draft.get("video_path", "")) == generated_path for draft in drafts):
            drafts = [
                {
                    "account_id": account["id"],
                    "video_path": generated_path,
                    "metadata": generated.get("metadata", {}),
                    "created_at": "Current session",
                }
            ] + drafts

    workspace_col, status_col = st.columns([1.35, 1])
    with workspace_col:
        render_panel(
            "Creation Workspace",
            "Use Create for the current run, Preview for quality control, and Draft Library for uploads and reuse. This keeps the page shorter and the next action easier to find.",
            soft=True,
        )
    with status_col:
        render_kv_card(
            "Channel Workspace",
            account.get("nickname", "YouTube account"),
            [
                ("Niche", str(account.get("niche", "") or "Not set")),
                ("Language", str(account.get("language", "English"))),
                ("Dialect", str(account.get("dialect", "") or "Default")),
                ("Made for kids", "Yes" if account.get("is_for_kids", get_is_for_kids()) else "No"),
                ("Drafts", str(len(drafts))),
            ],
        )

    create_tab, preview_tab, drafts_tab, pricing_tab = st.tabs(["Create", "Preview", "Draft Library", "Pricing"])

    with create_tab:
        setup_col, context_col = st.columns([1.2, 1])
        with setup_col:
            niche_override = st.text_input("Niche override", value=account.get("niche", ""))
            language_override = st.text_input("Language override", value=account.get("language", "English"))
            dialect_override = st.text_input("Dialect override", value=account.get("dialect", ""))
        with context_col:
            context_override = st.text_area(
                "Character context override",
                value=account.get("character_context", ""),
                height=180,
            )

        script_mode = st.radio(
            "Script source",
            options=["Generate with AI", "Write manually"],
            horizontal=True,
            key=f"youtube_script_mode_{account['id']}",
        )
        manual_subject = ""
        manual_script = ""
        if script_mode == "Write manually":
            st.markdown(
                """
                <div class="studio-note">
                    Manual script mode is on. Paste your final narration below and the app will generate the title,
                    description, image prompts, images, TTS, subtitles, and final video around your script.
                </div>
                """,
                unsafe_allow_html=True,
            )
            manual_subject = st.text_input(
                "Manual topic / angle (optional)",
                key=f"youtube_manual_subject_{account['id']}",
                help="If you leave this empty, the app will derive a subject from the script itself.",
            )
            manual_script = st.text_area(
                "Manual video script",
                key=f"youtube_manual_script_{account['id']}",
                height=220,
                help="Paste the exact narration you want. The app will generate metadata, images, TTS, and the final video around it.",
            )
        else:
            st.markdown(
                """
                <div class="studio-note">
                    AI script mode is on. Generate a preview first when you want a quick quality check, then create the full video only when the direction feels right.
                </div>
                """,
                unsafe_allow_html=True,
            )

        col1, col2, col3 = st.columns(3)

        if col1.button("Generate Idea + Script Preview", use_container_width=True):
            try:
                provider, model = ensure_llm_selected()
                youtube = YouTube(
                    account["id"],
                    account["nickname"],
                    account["firefox_profile"],
                    niche_override,
                    language_override,
                    dialect_override,
                    context_override,
                    account.get("is_for_kids"),
                    open_browser=False,
                )
                if script_mode == "Write manually":
                    if not manual_script.strip():
                        raise RuntimeError("Enter the manual script first.")
                    youtube.set_manual_script(manual_script, manual_subject)
                    subject = youtube.subject
                    script = youtube.script
                else:
                    subject = youtube.generate_topic()
                    script = youtube.generate_script()
                metadata = youtube.generate_metadata()
                st.session_state["youtube_preview"] = {
                    "account_id": account["id"],
                    "subject": subject,
                    "script": script,
                    "metadata": metadata,
                }
                st.success(f"Preview generated with {provider}:{model}")
            except Exception as exc:
                st.error(str(exc))
                st.code(traceback.format_exc())

        if col2.button("Generate Full Video", use_container_width=True):
            try:
                provider, model = ensure_llm_selected()
                youtube = YouTube(
                    account["id"],
                    account["nickname"],
                    account["firefox_profile"],
                    niche_override,
                    language_override,
                    dialect_override,
                    context_override,
                    account.get("is_for_kids"),
                    open_browser=False,
                )
                with st.spinner("Generating video assets..."):
                    if script_mode == "Write manually":
                        if not manual_script.strip():
                            raise RuntimeError("Enter the manual script first.")
                        youtube.generate_video_from_existing_script(TTS(), manual_script, manual_subject)
                    else:
                        youtube.generate_video(TTS())

                st.session_state["youtube_preview"] = {
                    "account_id": account["id"],
                    "subject": getattr(youtube, "subject", ""),
                    "script": getattr(youtube, "script", ""),
                    "metadata": youtube.metadata,
                }
                st.session_state["youtube_generated_video"] = {
                    "account_id": account["id"],
                    "video_path": youtube.video_path,
                    "metadata": youtube.metadata,
                    "subject": getattr(youtube, "subject", ""),
                    "script": getattr(youtube, "script", ""),
                }
                save_youtube_draft(account["id"], youtube.video_path, youtube.metadata)
                st.success(f"Video generated with {provider}:{model}")
            except ImageRateLimitError as exc:
                st.error(str(exc))
                st.info(
                    "The GUI stopped early because the image API is quota-limited right now. "
                    "Try again later, lower image demand, or upgrade the active image provider quota."
                )
            except Exception as exc:
                st.error(str(exc))
                with st.expander("Technical details"):
                    st.code(traceback.format_exc())

        if col3.button("Upload Latest Draft", use_container_width=True):
            try:
                generated_for_upload = st.session_state.get("youtube_generated_video")
                if not generated_for_upload or generated_for_upload.get("account_id") != account["id"]:
                    generated_for_upload = get_last_youtube_draft(account["id"])

                if not generated_for_upload:
                    raise RuntimeError("Generate a video for this account first.")

                youtube = YouTube(
                    account["id"],
                    account["nickname"],
                    account["firefox_profile"],
                    niche_override,
                    language_override,
                    dialect_override,
                    context_override,
                    account.get("is_for_kids"),
                    open_browser=True,
                )
                youtube.video_path = generated_for_upload["video_path"]
                youtube.metadata = generated_for_upload["metadata"]
                uploaded = youtube.upload_video()
                if uploaded:
                    st.success("Video uploaded.")
                else:
                    st.error("Upload failed.")
            except Exception as exc:
                st.error(str(exc))
                st.code(traceback.format_exc())

    with preview_tab:
        preview_payload = preview if preview.get("account_id") == account["id"] else {}
        generated_payload = generated if generated.get("account_id") == account["id"] else {}

        if preview_payload or generated_payload:
            metadata = (
                preview_payload.get("metadata", {})
                if preview_payload
                else generated_payload.get("metadata", {})
            )
            subject_value = (
                preview_payload.get("subject", "")
                if preview_payload
                else generated_payload.get("subject", "")
            )
            script_value = (
                preview_payload.get("script", "")
                if preview_payload
                else generated_payload.get("script", "")
            )
            preview_col, metadata_col = st.columns([1.15, 1])
            with preview_col:
                panel_copy = (
                    "Review the direction here before you spend time and credits on full rendering."
                    if preview_payload
                    else "This preview was reconstructed from the latest full video generation for the current account."
                )
                render_panel("Topic + Script Preview", panel_copy, soft=True)
                st.text_input("Topic", value=subject_value, disabled=True, key=f"yt_preview_topic_{account['id']}")
                st.text_area("Script", value=script_value, height=260, disabled=True, key=f"yt_preview_script_{account['id']}")
                generated_path = os.path.abspath(generated_payload.get("video_path", "")) if generated_payload else ""
                if generated_path and os.path.exists(generated_path):
                    st.video(generated_path)
            with metadata_col:
                render_panel("Metadata Preview", "Title, description, hashtags, and tags currently attached to this preview.", soft=True)
                st.text_input("Title", value=metadata.get("title", ""), disabled=True, key=f"yt_preview_title_{account['id']}")
                st.text_area("Description", value=metadata.get("description", ""), height=160, disabled=True, key=f"yt_preview_description_{account['id']}")
                render_chip_row("Hashtags", metadata.get("hashtags", []))
                render_chip_row("Tags", metadata.get("tags", []), muted=True)
        else:
            st.info("No preview yet. Generate an idea/script preview from the Create tab.")

    with drafts_tab:
        st.markdown("#### Draft Library")
        if drafts:
            selected_draft_path = st.selectbox(
                "Choose a draft",
                options=[draft["video_path"] for draft in drafts],
                format_func=lambda path: format_draft_option(
                    next(draft for draft in drafts if draft["video_path"] == path)
                ),
                key=f"youtube_draft_select_{account['id']}",
            )
            selected_draft = next(draft for draft in drafts if draft["video_path"] == selected_draft_path)
            selected_metadata = selected_draft.get("metadata", {})

            preview_col, details_col = st.columns([1.25, 1])
            with preview_col:
                st.markdown(
                    f"""
                    <div class="studio-card">
                        <div class="studio-eyebrow">Selected Draft</div>
                        <div class="studio-title" dir="auto">{escape(str(selected_metadata.get("title", "(untitled)")))}</div>
                        <div class="studio-subtitle">
                            {escape(str(selected_draft.get("created_at", "")).replace("T", " "))}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if os.path.exists(selected_draft_path):
                    st.video(selected_draft_path)
                st.markdown(
                    f'<div class="studio-path">{escape(selected_draft_path)}</div>',
                    unsafe_allow_html=True,
                )

            with details_col:
                render_kv_card(
                    "Video Summary",
                    str(selected_metadata.get("title", "(untitled)")),
                    [
                        ("Language", str(account.get("language", "English"))),
                        ("Dialect", str(account.get("dialect", "") or "Default")),
                        ("Niche", str(account.get("niche", ""))),
                        ("Drafts", str(len(drafts))),
                    ],
                )
                st.text_area(
                    "Description",
                    value=selected_metadata.get("description", ""),
                    height=170,
                    disabled=True,
                    key=f"youtube_selected_description_{account['id']}",
                )

                upload_col, info_col = st.columns([1.2, 0.8])
                if upload_col.button("Upload Selected Draft", use_container_width=True, key=f"upload_selected_draft_{account['id']}"):
                    try:
                        youtube = YouTube(
                            account["id"],
                            account["nickname"],
                            account["firefox_profile"],
                            account.get("niche", ""),
                            account.get("language", "English"),
                            account.get("dialect", ""),
                            account.get("character_context", ""),
                            account.get("is_for_kids"),
                            open_browser=True,
                        )
                        youtube.video_path = selected_draft_path
                        youtube.metadata = selected_metadata
                        if youtube.upload_video():
                            st.success("Video uploaded.")
                        else:
                            st.error("Upload failed.")
                    except Exception as exc:
                        st.error(str(exc))
                        with st.expander("Technical details", expanded=False):
                            st.code(traceback.format_exc())

                info_col.metric("Drafts", len(drafts))
                selected_pricing = load_pricing_for_video(selected_draft_path, selected_metadata)
                if selected_pricing:
                    info_col.metric(
                        "Estimated Cost",
                        f"{float(selected_pricing.get('total_estimated_cost', 0.0) or 0.0):.4f} {selected_pricing.get('currency', 'USD')}",
                    )
                render_chip_row("Hashtags", selected_metadata.get("hashtags", []))
                render_chip_row("Tags", selected_metadata.get("tags", []), muted=True)
        else:
            st.info("No saved drafts yet. Generate a video or import an existing MP4 to start building a draft library.")

        with st.expander("Import Existing Video File"):
            with st.form(f"import_video_{account['id']}"):
                import_video_path = st.text_input(
                    "MP4 path",
                    value="",
                    help="Use this to recover a previously generated MP4 and make it uploadable from the GUI.",
                )
                import_title = st.text_input("Title", value=preview.get("metadata", {}).get("title", ""))
                import_description = st.text_area(
                    "Description",
                    value=preview.get("metadata", {}).get("description", ""),
                    height=120,
                )
                import_button = st.form_submit_button("Save As Draft", use_container_width=True)

            if import_button:
                normalized_import_path = os.path.abspath(import_video_path.strip())
                if not import_video_path.strip():
                    st.error("Enter the MP4 path first.")
                elif not os.path.exists(normalized_import_path):
                    st.error("That MP4 path does not exist.")
                else:
                    save_youtube_draft(
                        account["id"],
                        normalized_import_path,
                        {
                            "title": import_title.strip(),
                            "description": import_description.strip(),
                        },
                    )
                    st.success("Video draft saved. You can now use Upload Latest Draft.")
                    st.rerun()

        recent_videos = account.get("videos", [])
        if recent_videos:
            with st.expander("Recent cached uploads"):
                for video in reversed(recent_videos[-10:]):
                    st.write(f'[{video.get("date", "")}] {video.get("title", "")}')
                    st.write(video.get("url", ""))

    with pricing_tab:
        st.markdown("#### Pricing Ledger")
        current_generated = st.session_state.get("youtube_generated_video", {})
        generated_path = ""
        generated_metadata = {}
        if current_generated.get("account_id") == account["id"]:
            generated_path = current_generated.get("video_path", "")
            generated_metadata = current_generated.get("metadata", {})

        source_options: list[tuple[str, str, dict]] = []
        if generated_path:
            source_options.append(("Current session video", generated_path, generated_metadata))
        for draft in drafts:
            source_options.append((format_draft_option(draft), draft.get("video_path", ""), draft.get("metadata", {})))

        deduped_options = []
        seen_paths = set()
        for label, path, metadata in source_options:
            normalized = os.path.abspath(str(path or ""))
            if not normalized or normalized in seen_paths:
                continue
            seen_paths.add(normalized)
            deduped_options.append((label, normalized, metadata))

        if deduped_options:
            selected_cost_path = st.selectbox(
                "Choose a generated video",
                options=[path for _, path, _ in deduped_options],
                format_func=lambda selected_path: next(
                    label for label, path, _ in deduped_options if path == selected_path
                ),
                key=f"youtube_pricing_select_{account['id']}",
            )
            selected_cost_metadata = next(
                metadata for _, path, metadata in deduped_options if path == selected_cost_path
            )
            render_panel(
                "Estimated Spend",
                "This report sums text generation, image generation, TTS, and subtitle processing for the selected video run. Free assets still appear so you can see what cost zero.",
                soft=True,
            )
            render_pricing_report(
                load_pricing_for_video(selected_cost_path, selected_cost_metadata),
                key_prefix=f"youtube_pricing_{account['id']}",
            )
        else:
            st.info("Generate or save a draft first to see its pricing breakdown.")


def render_tiktok_studio() -> None:
    render_section_intro(
        "TikTok Studio",
        "Choose an existing source video or generate a new one here, then shape the caption before uploading through your saved browser profile.",
        eyebrow="Short-Form Repurposing",
    )
    accounts = get_accounts("tiktok")
    if not accounts:
        st.info("Create a TikTok account first.")
        return

    account_id = st.selectbox(
        "TikTok account",
        options=[account["id"] for account in accounts],
        format_func=lambda selected_id: next(
            f'{account["nickname"]} - {account.get("niche", "")}'
            for account in accounts
            if account["id"] == selected_id
        ),
        key="tiktok_studio_account_select",
    )
    account = next(account for account in accounts if account["id"] == account_id)

    niche_override = st.text_input("Niche override", value=account.get("niche", ""), key="tiktok_niche_override")
    language_override = st.text_input("Language override", value=account.get("language", "English"), key="tiktok_language_override")
    dialect_override = st.text_input("Dialect override", value=account.get("dialect", ""), key="tiktok_dialect_override")
    context_override = st.text_area(
        "Character context override",
        value=account.get("character_context", ""),
        key="tiktok_context_override",
    )
    script_mode = st.radio(
        "Script source",
        options=["Generate with AI", "Write manually"],
        horizontal=True,
        key=f"tiktok_script_mode_{account['id']}",
    )
    manual_subject = ""
    manual_script = ""
    if script_mode == "Write manually":
        manual_subject = st.text_input(
            "Manual topic / angle (optional)",
            key=f"tiktok_manual_subject_{account['id']}",
            help="If left empty, the app will derive the topic from the script.",
        )
        manual_script = st.text_area(
            "Manual video script",
            key=f"tiktok_manual_script_{account['id']}",
            height=220,
            help="Paste the narration you want, then let the app generate the rest of the video around it.",
        )

    render_kv_card(
        "TikTok Workspace",
        account.get("nickname", "TikTok account"),
        [
            ("Niche", niche_override or "Not set"),
            ("Language", language_override or "Not set"),
            ("Dialect", dialect_override or "Default"),
            ("Cached uploads", str(len(account.get("videos", [])))),
        ],
    )

    preview_state_key = f"tiktok_video_preview_{account['id']}"
    generated_state_key = f"tiktok_generated_video_{account['id']}"
    source_mode = st.radio(
        "Video source",
        options=["Use YouTube draft", "Generate new video here"],
        horizontal=True,
        key=f"tiktok_source_mode_{account['id']}",
    )

    youtube_drafts = get_all_youtube_drafts()
    source_path = ""
    source_metadata = {}

    if source_mode == "Generate new video here":
        action_col1, action_col2 = st.columns(2)

        if action_col1.button(
            "Generate Idea + Script Preview",
            use_container_width=True,
            key=f"tiktok_generate_preview_{account['id']}",
        ):
            try:
                provider, model = ensure_llm_selected()
                youtube = YouTube(
                    account["id"],
                    account["nickname"],
                    account["firefox_profile"],
                    niche_override,
                    language_override,
                    dialect_override,
                    context_override,
                    account.get("is_for_kids"),
                    open_browser=False,
                )
                if script_mode == "Write manually":
                    if not manual_script.strip():
                        raise RuntimeError("Enter the manual script first.")
                    youtube.set_manual_script(manual_script, manual_subject)
                    subject = youtube.subject
                    script = youtube.script
                else:
                    subject = youtube.generate_topic()
                    script = youtube.generate_script()
                metadata = youtube.generate_metadata()
                st.session_state[preview_state_key] = {
                    "account_id": account["id"],
                    "subject": subject,
                    "script": script,
                    "metadata": metadata,
                }
                st.success(f"Preview generated with {provider}:{model}")
            except Exception as exc:
                st.error(str(exc))
                with st.expander("Technical details", expanded=False):
                    st.code(traceback.format_exc())

        if action_col2.button(
            "Generate Full Video For TikTok",
            use_container_width=True,
            key=f"tiktok_generate_video_{account['id']}",
        ):
            try:
                provider, model = ensure_llm_selected()
                youtube = YouTube(
                    account["id"],
                    account["nickname"],
                    account["firefox_profile"],
                    niche_override,
                    language_override,
                    dialect_override,
                    context_override,
                    account.get("is_for_kids"),
                    open_browser=False,
                )
                with st.spinner("Generating TikTok video assets..."):
                    if script_mode == "Write manually":
                        if not manual_script.strip():
                            raise RuntimeError("Enter the manual script first.")
                        youtube.generate_video_from_existing_script(TTS(), manual_script, manual_subject)
                    else:
                        youtube.generate_video(TTS())

                st.session_state[generated_state_key] = {
                    "account_id": account["id"],
                    "video_path": youtube.video_path,
                    "metadata": youtube.metadata,
                }
                save_youtube_draft(account["id"], youtube.video_path, youtube.metadata)
                st.success(f"Video generated with {provider}:{model}")
            except ImageRateLimitError as exc:
                st.error(str(exc))
                st.info(
                    "The image provider is quota-limited right now. Try again later, lower image demand, or switch providers."
                )
            except Exception as exc:
                st.error(str(exc))
                with st.expander("Technical details", expanded=False):
                    st.code(traceback.format_exc())

        preview = st.session_state.get(preview_state_key, {})
        if preview.get("account_id") == account["id"]:
            with st.expander("Generated preview", expanded=False):
                st.text_input(
                    "Topic",
                    value=preview.get("subject", ""),
                    disabled=True,
                    key=f"tiktok_preview_subject_{account['id']}",
                )
                st.text_area(
                    "Script",
                    value=preview.get("script", ""),
                    height=180,
                    disabled=True,
                    key=f"tiktok_preview_script_{account['id']}",
                )
                preview_metadata = preview.get("metadata", {})
                st.text_input(
                    "Title",
                    value=preview_metadata.get("title", ""),
                    disabled=True,
                    key=f"tiktok_preview_title_{account['id']}",
                )
                st.text_area(
                    "Description",
                    value=preview_metadata.get("description", ""),
                    height=120,
                    disabled=True,
                    key=f"tiktok_preview_description_{account['id']}",
                )

        generated = st.session_state.get(generated_state_key, {})
        if generated.get("account_id") == account["id"] and generated.get("video_path"):
            generated_path = os.path.abspath(generated.get("video_path", ""))
            if os.path.exists(generated_path):
                source_path = generated_path
                source_metadata = generated.get("metadata", {})
                st.success("Using the video generated in TikTok Studio for this upload.")
            else:
                st.warning("The last generated TikTok Studio video is no longer available on disk.")
        else:
            st.info("Generate a full video here to use it as the TikTok source, or switch to 'Use YouTube draft'.")
    else:
        if youtube_drafts:
            selected_source = st.selectbox(
                "Source video",
                options=[draft["video_path"] for draft in youtube_drafts],
                format_func=lambda path: format_draft_option(
                    next(draft for draft in youtube_drafts if draft["video_path"] == path)
                ),
                key=f"tiktok_source_draft_select_{account['id']}",
            )
            selected_draft = next(draft for draft in youtube_drafts if draft["video_path"] == selected_source)
            source_path = selected_draft["video_path"]
            source_metadata = selected_draft.get("metadata", {})
        else:
            st.info("No YouTube drafts found yet. You can still paste a video path manually below.")

    manual_source_path = st.text_input(
        "Or paste a video path manually",
        value="",
        help="Optional override if you want to upload a video that is not in the YouTube draft library.",
        key=f"tiktok_manual_source_path_{account['id']}",
    ).strip()
    if manual_source_path:
        source_path = os.path.abspath(manual_source_path)
        source_metadata = {}

    tiktok_helper = TikTok(
        account["id"],
        account["nickname"],
        account["firefox_profile"],
        niche_override,
        language_override,
        dialect_override,
        context_override,
        open_browser=False,
    )

    caption_state_key = f"tiktok_caption_preview_{account['id']}"
    caption_editor_key = f"tiktok_caption_editor_{account['id']}"
    source_signature_key = f"tiktok_source_signature_{account['id']}"
    current_source_signature = json.dumps(
        {
            "mode": source_mode,
            "path": os.path.abspath(source_path) if source_path else "",
            "title": source_metadata.get("title", ""),
            "description": source_metadata.get("description", ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    default_caption = tiktok_helper.build_basic_caption(source_metadata) if source_metadata else ""

    if st.session_state.get(source_signature_key) != current_source_signature:
        st.session_state[source_signature_key] = current_source_signature
        st.session_state[caption_state_key] = default_caption
        st.session_state[caption_editor_key] = default_caption
    else:
        default_caption = st.session_state.get(caption_state_key, default_caption)

    action_col1, action_col2 = st.columns(2)
    if action_col1.button("Build Caption From Metadata", use_container_width=True):
        rebuilt_caption = tiktok_helper.build_basic_caption(source_metadata)
        st.session_state[caption_state_key] = rebuilt_caption
        st.session_state[caption_editor_key] = rebuilt_caption

    if action_col2.button("Refine Caption With AI", use_container_width=True):
        try:
            provider, model = ensure_llm_selected()
            refined = tiktok_helper.generate_caption(source_metadata)
            st.session_state[caption_state_key] = refined
            st.session_state[caption_editor_key] = refined
            st.success(f"TikTok caption refined with {provider}:{model}")
        except Exception as exc:
            st.error(str(exc))
            with st.expander("Technical details", expanded=False):
                st.code(traceback.format_exc())

    caption_preview = st.text_area(
        "TikTok caption",
        value=st.session_state.get(caption_state_key, default_caption),
        height=150,
        key=caption_editor_key,
    )
    st.session_state[caption_state_key] = caption_preview

    preview_col, detail_col = st.columns([1.35, 1])
    with preview_col:
        st.markdown(
            f"""
            <div class="studio-card">
                <div class="studio-eyebrow">Upload Target</div>
                <div class="studio-title" dir="auto">{escape(account["nickname"])}</div>
                <div class="studio-subtitle"><strong>Language:</strong> {escape(language_override or account.get("language", "English"))}</div>
                <div class="studio-subtitle"><strong>Dialect:</strong> {escape(dialect_override or account.get("dialect", "") or "Default")}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if source_path and os.path.exists(source_path):
            st.video(source_path)
            st.markdown(f'<div class="studio-path">{escape(source_path)}</div>', unsafe_allow_html=True)
        else:
            st.info("Choose or paste a valid source video to preview it here.")

    with detail_col:
        st.markdown(
            f"""
            <div class="studio-card">
                <div class="studio-eyebrow">Source Metadata</div>
                <div class="studio-title" dir="auto">{escape(str(source_metadata.get("title", "(untitled)")))}</div>
                <div class="studio-subtitle">{escape(str(source_metadata.get("description", "")))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        render_chip_row("Hashtags", source_metadata.get("hashtags", []))
        render_chip_row("Tags", source_metadata.get("tags", []), muted=True)
        source_pricing = load_pricing_for_video(source_path, source_metadata) if source_path else {}
        if source_pricing:
            with st.expander("Source Video Pricing", expanded=False):
                render_pricing_report(
                    source_pricing,
                    key_prefix=f"tiktok_source_pricing_{account['id']}",
                )

        if st.button("Upload To TikTok", use_container_width=True, key="tiktok_upload_button"):
            try:
                if not source_path or not os.path.exists(source_path):
                    raise RuntimeError("Choose a valid source video before uploading to TikTok.")

                uploader = TikTok(
                    account["id"],
                    account["nickname"],
                    account["firefox_profile"],
                    niche_override,
                    language_override,
                    dialect_override,
                    context_override,
                    open_browser=True,
                )
                uploader.upload_video(source_path, caption_preview.strip())
                st.success("Video uploaded to TikTok.")
            except Exception as exc:
                st.error(str(exc))
                with st.expander("Technical details", expanded=False):
                    st.code(traceback.format_exc())

    recent_videos = account.get("videos", [])
    if recent_videos:
        with st.expander("Recent cached TikTok uploads"):
            for video in reversed(recent_videos[-10:]):
                st.write(f'[{video.get("date", "")}] {video.get("caption", "")[:100]}')
                if video.get("url"):
                    st.write(video.get("url", ""))


def main() -> None:
    st.set_page_config(
        page_title="MoneyPrinter V2 Studio",
        layout="wide",
    )

    inject_app_styles()

    tabs = st.tabs(
        [
            "Overview",
            "Config",
            "Twitter Accounts",
            "YouTube Accounts",
            "TikTok Accounts",
            "Twitter Studio",
            "YouTube Studio",
            "TikTok Studio",
        ]
    )

    with tabs[0]:
        render_overview()
    with tabs[1]:
        render_config_tab()
    with tabs[2]:
        render_account_editor("twitter")
    with tabs[3]:
        render_account_editor("youtube")
    with tabs[4]:
        render_account_editor("tiktok")
    with tabs[5]:
        render_twitter_studio()
    with tabs[6]:
        render_youtube_studio()
    with tabs[7]:
        render_tiktok_studio()


if __name__ == "__main__":
    main()
