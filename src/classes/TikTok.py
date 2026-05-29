import os
import time
import json

from cache import get_tiktok_cache_path
from config import get_headless, get_llm_provider, get_verbose, get_youtube_metadata_model
from status import error, info, success, warning
from llm_provider import generate_text
from typing import Optional, List
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class TikTok:
    """
    First-pass TikTok uploader that reuses generated videos and posts them
    through the TikTok web uploader.
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
        open_browser: bool = True,
    ) -> None:
        self.account_uuid = account_uuid
        self.account_nickname = account_nickname
        self.fp_profile_path = fp_profile_path
        self.niche = niche
        self.language = language
        self.dialect = dialect.strip()
        self.character_context = character_context.strip()
        self.browser: Optional[webdriver.Firefox] = None
        self.wait: Optional[WebDriverWait] = None
        self.video_path: str = ""
        self.caption: str = ""
        self._keep_browser_open = False

        self.options: Options = Options()
        if get_headless():
            self.options.add_argument("--headless")

        if open_browser:
            if not os.path.isdir(fp_profile_path):
                raise ValueError(
                    f"Firefox profile path does not exist or is not a directory: {fp_profile_path}"
                )

            self._assert_profile_is_available(fp_profile_path)
            self.options.add_argument("-profile")
            self.options.add_argument(fp_profile_path)

            self.service: Service = Service(GeckoDriverManager().install())
            self.browser = webdriver.Firefox(
                service=self.service,
                options=self.options,
            )
            self.wait = WebDriverWait(self.browser, 30)

    def _assert_profile_is_available(self, profile_path: str) -> None:
        lock_path = os.path.join(profile_path, "parent.lock")
        if os.path.exists(lock_path):
            raise RuntimeError(
                "The selected Firefox profile is currently in use. Close Firefox completely and try again."
            )

    def _get_caption_model_name(self) -> str | None:
        configured_model = get_youtube_metadata_model().strip()
        if configured_model:
            return configured_model

        if get_llm_provider() == "openai":
            return "gpt-5-nano"

        return None

    def _get_locale_block(self) -> str:
        dialect_line = f"Dialect/Style: {self.dialect}\n" if self.dialect else ""
        return (
            f"Language: {self.language}\n"
            f"{dialect_line}"
            "If a dialect/style is specified, keep the wording native and natural to that dialect.\n"
        )

    def build_basic_caption(self, metadata: dict | None = None) -> str:
        metadata = metadata or {}
        title = str(metadata.get("title", "")).strip()
        description = str(metadata.get("description", "")).strip()
        hashtags = [
            hashtag if str(hashtag).startswith("#") else f"#{str(hashtag).strip()}"
            for hashtag in metadata.get("hashtags", [])
            if str(hashtag).strip()
        ][:4]

        caption_parts = []
        if title:
            caption_parts.append(title)
        elif description:
            caption_parts.append(description[:120].strip())

        if hashtags:
            caption_parts.append(" ".join(hashtags))

        caption = "\n".join(part for part in caption_parts if part).strip()
        return caption[:2200]

    def generate_caption(self, metadata: dict | None = None) -> str:
        metadata = metadata or {}
        prompt = f"""
        Generate one natural TikTok caption for a short-form video.
        {self._get_locale_block()}
        Niche: {self.niche}
        Character context: {self.character_context}

        Video title: {metadata.get("title", "")}
        Video description: {metadata.get("description", "")}
        Hashtags: {", ".join(metadata.get("hashtags", []))}
        Tags: {", ".join(metadata.get("tags", []))}

        Rules:
        - Keep it concise and natural for TikTok.
        - Use 2 to 4 relevant hashtags at the end.
        - Do not use markdown.
        - Return only the final caption.
        """
        caption = generate_text(prompt, model_name=self._get_caption_model_name()).strip()
        self.caption = caption
        return caption

    def _replace_textbox_value(self, element, text: str) -> None:
        driver = self.browser
        if driver is None:
            raise RuntimeError("Browser session is not initialized.")

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        element.click()
        time.sleep(0.5)
        element.send_keys(Keys.CONTROL, "a")
        time.sleep(0.2)
        element.send_keys(Keys.DELETE)
        time.sleep(0.2)
        element.send_keys(text)

    def _find_element_across_frames(self, selectors: list[tuple[str, str]], clickable: bool = False):
        if self.browser is None:
            raise RuntimeError("Browser session is not initialized.")

        driver = self.browser
        contexts = [None] + driver.find_elements(By.TAG_NAME, "iframe")

        for context in contexts:
            driver.switch_to.default_content()
            if context is not None:
                try:
                    driver.switch_to.frame(context)
                except Exception:
                    continue

            for by, value in selectors:
                try:
                    if clickable:
                        return WebDriverWait(driver, 10).until(
                            EC.element_to_be_clickable((by, value))
                        )
                    return WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((by, value))
                    )
                except Exception:
                    continue

        driver.switch_to.default_content()
        return None

    def add_video(self, video: dict) -> None:
        cache_path = get_tiktok_cache_path()
        if not os.path.exists(cache_path):
            with open(cache_path, "w", encoding="utf-8") as file:
                json.dump({"accounts": []}, file, indent=4, ensure_ascii=False)

        with open(cache_path, "r", encoding="utf-8") as file:
            parsed = json.load(file) or {"accounts": []}

        accounts = parsed.get("accounts", [])
        for account in accounts:
            if account.get("id") == self.account_uuid:
                account.setdefault("videos", []).append(video)
                break

        with open(cache_path, "w", encoding="utf-8") as file:
            json.dump({"accounts": accounts}, file, indent=4, ensure_ascii=False)

    def get_videos(self) -> List[dict]:
        cache_path = get_tiktok_cache_path()
        if not os.path.exists(cache_path):
            return []

        with open(cache_path, "r", encoding="utf-8") as file:
            parsed = json.load(file) or {"accounts": []}

        for account in parsed.get("accounts", []):
            if account.get("id") == self.account_uuid:
                return account.get("videos", [])

        return []

    def upload_video(self, video_path: str | None = None, caption: str | None = None) -> bool:
        if self.browser is None:
            raise RuntimeError(
                "TikTok browser session is not initialized. Recreate this account session with browser access enabled."
            )

        if video_path:
            self.video_path = os.path.abspath(video_path)
        if caption is not None:
            self.caption = caption

        if not self.video_path or not os.path.exists(self.video_path):
            raise RuntimeError("TikTok upload requires a valid video path.")

        driver = self.browser
        verbose = get_verbose()

        try:
            driver.get("https://www.tiktok.com/upload?lang=en")
            time.sleep(5)

            file_input = self._find_element_across_frames(
                [
                    (By.CSS_SELECTOR, "input[type='file']"),
                ]
            )
            if file_input is None:
                raise RuntimeError(
                    "Could not find TikTok's video file input. Make sure you are logged into TikTok in this Firefox profile."
                )

            if verbose:
                info("\t=> Uploading video to TikTok...")
            file_input.send_keys(self.video_path)

            time.sleep(10)

            caption_box = self._find_element_across_frames(
                [
                    (By.CSS_SELECTOR, "div[data-e2e='video-caption'] div[contenteditable='true']"),
                    (By.CSS_SELECTOR, "div[contenteditable='true'][spellcheck='false']"),
                    (By.CSS_SELECTOR, "div[role='textbox'][contenteditable='true']"),
                    (By.CSS_SELECTOR, "textarea"),
                ]
            )
            if caption_box is None:
                raise RuntimeError("Could not find the TikTok caption editor.")

            if verbose:
                info("\t=> Setting TikTok caption...")
            self._replace_textbox_value(caption_box, self.caption)

            post_button = self._find_element_across_frames(
                [
                    (By.CSS_SELECTOR, "button[data-e2e='post_video_button']"),
                    (By.CSS_SELECTOR, "button[data-e2e='upload-post']"),
                    (By.XPATH, "//button[contains(., 'Post')]"),
                    (By.XPATH, "//button[contains(., 'Publish')]"),
                ],
                clickable=True,
            )
            if post_button is None:
                raise RuntimeError("Could not find the TikTok Post button.")

            if verbose:
                info("\t=> Posting video to TikTok...")
            post_button.click()
            time.sleep(6)

            self.add_video(
                {
                    "caption": self.caption,
                    "video_path": self.video_path,
                    "url": driver.current_url,
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

            success("Uploaded video to TikTok successfully!")
            self._keep_browser_open = True
            return True
        except Exception as exc:
            if verbose:
                error(f"TikTok upload failed: {exc}")
            raise RuntimeError(f"TikTok upload failed: {exc}") from exc

    def __del__(self) -> None:
        try:
            if self.browser and not self._keep_browser_open:
                self.browser.quit()
        except Exception:
            pass
