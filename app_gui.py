import json
import os
import sys
import traceback
from uuid import uuid4

import streamlit as st

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from cache import add_account, get_accounts, remove_account, update_account
from classes.Tts import TTS
from classes.Twitter import Twitter
from classes.YouTube import YouTube
from config import ROOT_DIR as APP_ROOT_DIR
from config import get_configured_llm_model, get_llm_provider
from llm_provider import list_models, select_model

CONFIG_PATH = os.path.join(APP_ROOT_DIR, "config.json")


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=2, ensure_ascii=False)


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


def render_overview() -> None:
    st.title("MoneyPrinter V2 Studio")
    st.caption("A first GUI layer for config, accounts, Twitter, and YouTube workflows.")

    config = load_config()
    twitter_accounts = get_accounts("twitter")
    youtube_accounts = get_accounts("youtube")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Twitter Accounts", len(twitter_accounts))
    col2.metric("YouTube Accounts", len(youtube_accounts))
    col3.metric("LLM Provider", config.get("llm_provider", "ollama"))
    col4.metric(
        "Active Model",
        config.get("openai_model") if config.get("llm_provider") == "openai" else config.get("ollama_model"),
    )

    st.info(
        "Close Firefox before using Post or Upload actions. Preview/generation actions work without launching the browser."
    )


def render_config_tab() -> None:
    st.subheader("Config")
    config = load_config()

    with st.form("config_form"):
        left, right = st.columns(2)

        with left:
            config["firefox_profile"] = st.text_input(
                "Default Firefox profile",
                value=config.get("firefox_profile", ""),
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
            config["openai_base_url"] = st.text_input(
                "OpenAI base URL",
                value=config.get("openai_base_url", "https://api.openai.com/v1"),
            )
            config["openai_api_key"] = st.text_input(
                "OpenAI API key",
                value=config.get("openai_api_key", ""),
                type="password",
            )
            config["ollama_base_url"] = st.text_input(
                "Ollama base URL",
                value=config.get("ollama_base_url", "http://127.0.0.1:11434"),
            )
            config["twitter_language"] = st.text_input(
                "Twitter language",
                value=config.get("twitter_language", "English"),
            )
            config["twitter_dialect"] = st.text_input(
                "Twitter dialect",
                value=config.get("twitter_dialect", ""),
            )

        submitted = st.form_submit_button("Save Config", use_container_width=True)

    if submitted:
        save_config(config)
        st.success("Config saved.")

    with st.expander("Raw config JSON"):
        st.code(json.dumps(config, ensure_ascii=False, indent=2), language="json")


def render_account_editor(provider: str) -> None:
    st.subheader(f"{provider.title()} Accounts")
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

        with st.form(f"edit_{provider}_account"):
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
                topic = st.text_input("Topic", value=selected_account.get("topic", ""))
            else:
                niche = st.text_input("Niche", value=selected_account.get("niche", ""))
                language = st.text_input("Language", value=selected_account.get("language", "English"))

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
    st.subheader(f"Create {provider.title()} Account")

    with st.form(f"create_{provider}_account"):
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
            niche = st.text_input("Niche", key="create_youtube_niche")
            language = st.text_input("Language", value="English", key="create_youtube_language")

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
            account["videos"] = []

        add_account(provider, account)
        st.success(f"{provider.title()} account created.")
        st.rerun()


def render_twitter_studio() -> None:
    st.subheader("Twitter Studio")
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
    st.subheader("YouTube Studio")
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

    niche_override = st.text_input("Niche override", value=account.get("niche", ""))
    language_override = st.text_input("Language override", value=account.get("language", "English"))
    context_override = st.text_area(
        "Character context override",
        value=account.get("character_context", ""),
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
                context_override,
                open_browser=False,
            )
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
                context_override,
                open_browser=False,
            )
            with st.spinner("Generating video assets..."):
                youtube.generate_video(TTS())

            st.session_state["youtube_generated_video"] = {
                "account_id": account["id"],
                "video_path": youtube.video_path,
                "metadata": youtube.metadata,
            }
            st.success(f"Video generated with {provider}:{model}")
        except Exception as exc:
            st.error(str(exc))
            st.code(traceback.format_exc())

    if col3.button("Upload Last Generated Video", use_container_width=True):
        try:
            generated = st.session_state.get("youtube_generated_video")
            if not generated or generated.get("account_id") != account["id"]:
                raise RuntimeError("Generate a video for this account first.")

            youtube = YouTube(
                account["id"],
                account["nickname"],
                account["firefox_profile"],
                niche_override,
                language_override,
                context_override,
                open_browser=True,
            )
            youtube.video_path = generated["video_path"]
            youtube.metadata = generated["metadata"]
            uploaded = youtube.upload_video()
            if uploaded:
                st.success("Video uploaded.")
            else:
                st.error("Upload failed.")
        except Exception as exc:
            st.error(str(exc))
            st.code(traceback.format_exc())

    preview = st.session_state.get("youtube_preview", {})
    if preview.get("account_id") == account["id"]:
        st.markdown("#### Preview")
        st.text_input("Topic", value=preview.get("subject", ""), disabled=True)
        st.text_area("Script", value=preview.get("script", ""), height=220, disabled=True)
        metadata = preview.get("metadata", {})
        st.text_input("Title", value=metadata.get("title", ""), disabled=True)
        st.text_area("Description", value=metadata.get("description", ""), height=120, disabled=True)

    generated = st.session_state.get("youtube_generated_video", {})
    if generated.get("account_id") == account["id"]:
        st.markdown("#### Last Generated Video")
        st.code(generated.get("video_path", ""))

    recent_videos = account.get("videos", [])
    if recent_videos:
        with st.expander("Recent cached uploads"):
            for video in reversed(recent_videos[-10:]):
                st.write(f'[{video.get("date", "")}] {video.get("title", "")}')
                st.write(video.get("url", ""))


def main() -> None:
    st.set_page_config(
        page_title="MoneyPrinter V2 Studio",
        layout="wide",
    )

    render_overview()

    tabs = st.tabs(
        [
            "Config",
            "Twitter Accounts",
            "YouTube Accounts",
            "Twitter Studio",
            "YouTube Studio",
        ]
    )

    with tabs[0]:
        render_config_tab()
    with tabs[1]:
        render_account_editor("twitter")
    with tabs[2]:
        render_account_editor("youtube")
    with tabs[3]:
        render_twitter_studio()
    with tabs[4]:
        render_youtube_studio()


if __name__ == "__main__":
    main()
