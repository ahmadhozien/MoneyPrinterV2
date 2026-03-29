import schedule
import subprocess
import json

from art import *
from cache import *
from utils import *
from config import *
from status import *
from uuid import uuid4
from constants import *
from classes.Tts import TTS
from termcolor import colored
from classes.TikTok import TikTok
from classes.Twitter import Twitter
from classes.YouTube import YouTube
from prettytable import PrettyTable
from classes.Outreach import Outreach
from classes.AFM import AffiliateMarketing
from llm_provider import list_models, select_model, get_active_model, get_active_provider

def main():
    """Main entry point for the application, providing a menu-driven interface
    to manage YouTube, Twitter bots, Affiliate Marketing, and Outreach tasks.

    This function allows users to:
    1. Start the YouTube Shorts Automater to manage YouTube accounts, 
       generate and upload videos, and set up CRON jobs.
    2. Start a Twitter Bot to manage Twitter accounts, post tweets, and 
       schedule posts using CRON jobs.
    3. Start a TikTok Service to manage TikTok accounts and upload videos.
    4. Manage Affiliate Marketing by creating pitches and sharing them via 
       Twitter accounts.
    5. Initiate an Outreach process for engagement and promotion tasks.
    6. Exit the application.

    The function continuously prompts users for input, validates it, and 
    executes the selected option until the user chooses to quit.

    Args:
        None

    Returns:
        None"""

    def load_youtube_drafts() -> list[dict]:
        drafts_path = os.path.join(ROOT_DIR, ".mp", "youtube_drafts.json")
        if not os.path.exists(drafts_path):
            return []

        try:
            with open(drafts_path, "r", encoding="utf-8") as file:
                payload = json.load(file) or {}
        except (OSError, json.JSONDecodeError):
            return []

        drafts = []
        for draft in payload.get("drafts", []):
            video_path = draft.get("video_path", "")
            if video_path and os.path.exists(video_path):
                drafts.append(draft)

        return sorted(drafts, key=lambda draft: draft.get("created_at", ""), reverse=True)

    # Get user input
    # user_input = int(question("Select an option: "))
    valid_input = False
    while not valid_input:
        try:
    # Show user options
            info("\n============ OPTIONS ============", False)

            for idx, option in enumerate(OPTIONS):
                print(colored(f" {idx + 1}. {option}", "cyan"))

            info("=================================\n", False)
            user_input = input("Select an option: ").strip()
            if user_input == '':
                print("\n" * 100)
                raise ValueError("Empty input is not allowed.")
            user_input = int(user_input)
            valid_input = True
        except ValueError as e:
            print("\n" * 100)
            print(f"Invalid input: {e}")


    # Start the selected option
    if user_input == 1:
        info("Starting YT Shorts Automater...")

        cached_accounts = get_accounts("youtube")

        if len(cached_accounts) == 0:
            warning("No accounts found in cache. Create one now?")
            user_input = question("Yes/No: ")

            if user_input.lower() == "yes":
                generated_uuid = str(uuid4())

                success(f" => Generated ID: {generated_uuid}")
                nickname = question(" => Enter a nickname for this account: ")
                fp_profile = question(" => Enter the path to the Firefox profile: ")
                niche = question(" => Enter the account niche: ")
                language = question(" => Enter the account language: ")
                dialect = question(" => Enter the account dialect/style (or leave empty): ").strip()
                character_context = question(
                    " => Enter the account character/context (tone, personality, audience): "
                )

                account_data = {
                    "id": generated_uuid,
                    "nickname": nickname,
                    "firefox_profile": fp_profile,
                    "niche": niche,
                    "language": language,
                    "dialect": dialect,
                    "character_context": character_context,
                    "is_for_kids": question(" => Is this YouTube account made for kids by default? (Yes/No): ").strip().lower() == "yes",
                    "videos": [],
                }

                add_account("youtube", account_data)

                success("Account configured successfully!")
        else:
            table = PrettyTable()
            table.field_names = ["ID", "UUID", "Nickname", "Niche"]

            for account in cached_accounts:
                table.add_row([cached_accounts.index(account) + 1, colored(account["id"], "cyan"), colored(account["nickname"], "blue"), colored(account["niche"], "green")])

            print(table)
            info("Type 'd' to delete an account.", False)

            user_input = question("Select an account to start (or 'd' to delete): ").strip()

            if user_input.lower() == "d":
                delete_input = question("Enter account number to delete: ").strip()
                account_to_delete = None

                for account in cached_accounts:
                    if str(cached_accounts.index(account) + 1) == delete_input:
                        account_to_delete = account
                        break

                if account_to_delete is None:
                    error("Invalid account selected. Please try again.", "red")
                else:
                    confirm = question(f"Are you sure you want to delete '{account_to_delete['nickname']}'? (Yes/No): ").strip().lower()

                    if confirm == "yes":
                        remove_account("youtube", account_to_delete["id"])
                        success("Account removed successfully!")
                    else:
                        warning("Account deletion canceled.", False)

                return

            selected_account = None

            for account in cached_accounts:
                if str(cached_accounts.index(account) + 1) == user_input:
                    selected_account = account

            if selected_account is None:
                error("Invalid account selected. Please try again.", "red")
                main()
            else:
                if "is_for_kids" not in selected_account:
                    migrated_is_for_kids = get_is_for_kids()
                    update_account(
                        "youtube",
                        selected_account["id"],
                        {"is_for_kids": migrated_is_for_kids},
                    )
                    selected_account["is_for_kids"] = migrated_is_for_kids

                if not selected_account.get("character_context", "").strip():
                    warning(
                        "This YouTube account has no character/context yet. Adding one will keep future videos more consistent."
                    )
                    character_context = question(
                        " => Enter the account character/context (or leave empty to skip): "
                    ).strip()
                    if character_context:
                        update_account(
                            "youtube",
                            selected_account["id"],
                            {"character_context": character_context},
                        )
                        selected_account["character_context"] = character_context
                        success("Saved account character/context.")

                youtube = YouTube(
                    selected_account["id"],
                    selected_account["nickname"],
                    selected_account["firefox_profile"],
                    selected_account["niche"],
                    selected_account["language"],
                    selected_account.get("dialect", ""),
                    selected_account.get("character_context", ""),
                    selected_account.get("is_for_kids"),
                )

                while True:
                    rem_temp_files()
                    info("\n============ OPTIONS ============", False)

                    for idx, youtube_option in enumerate(YOUTUBE_OPTIONS):
                        print(colored(f" {idx + 1}. {youtube_option}", "cyan"))

                    info("=================================\n", False)

                    # Get user input
                    user_input = int(question("Select an option: "))
                    tts = TTS()

                    if user_input == 1:
                        youtube.generate_video(tts)
                        upload_to_yt = question("Do you want to upload this video to YouTube? (Yes/No): ")
                        if upload_to_yt.lower() == "yes":
                            youtube.upload_video()
                    elif user_input == 2:
                        videos = youtube.get_videos()

                        if len(videos) > 0:
                            videos_table = PrettyTable()
                            videos_table.field_names = ["ID", "Date", "Title"]

                            for video in videos:
                                videos_table.add_row([
                                    videos.index(video) + 1,
                                    colored(video["date"], "blue"),
                                    colored(video["title"][:60] + "...", "green")
                                ])

                            print(videos_table)
                        else:
                            warning(" No videos found.")
                    elif user_input == 3:
                        info("How often do you want to upload?")

                        info("\n============ OPTIONS ============", False)
                        for idx, cron_option in enumerate(YOUTUBE_CRON_OPTIONS):
                            print(colored(f" {idx + 1}. {cron_option}", "cyan"))

                        info("=================================\n", False)

                        user_input = int(question("Select an Option: "))

                        cron_script_path = os.path.join(ROOT_DIR, "src", "cron.py")
                        command = [
                            "python",
                            cron_script_path,
                            "youtube",
                            selected_account['id'],
                            get_active_model(),
                            get_active_provider(),
                        ]

                        def job():
                            subprocess.run(command)

                        if user_input == 1:
                            # Upload Once
                            schedule.every(1).day.do(job)
                            success("Set up CRON Job.")
                        elif user_input == 2:
                            # Upload Twice a day
                            schedule.every().day.at("10:00").do(job)
                            schedule.every().day.at("16:00").do(job)
                            success("Set up CRON Job.")
                        else:
                            break
                    elif user_input == 4:
                        if get_verbose():
                            info(" => Climbing Options Ladder...", False)
                        break
    elif user_input == 2:
        info("Starting Twitter Bot...")

        cached_accounts = get_accounts("twitter")

        if len(cached_accounts) == 0:
            warning("No accounts found in cache. Create one now?")
            user_input = question("Yes/No: ")

            if user_input.lower() == "yes":
                generated_uuid = str(uuid4())

                success(f" => Generated ID: {generated_uuid}")
                nickname = question(" => Enter a nickname for this account: ")
                fp_profile = question(" => Enter the path to the Firefox profile: ")
                topic = question(" => Enter the account topic: ")
                character_context = question(
                    " => Enter the account character/context (tone, personality, audience): "
                )

                add_account("twitter", {
                    "id": generated_uuid,
                    "nickname": nickname,
                    "firefox_profile": fp_profile,
                    "topic": topic,
                    "character_context": character_context,
                    "posts": []
                })
        else:
            table = PrettyTable()
            table.field_names = ["ID", "UUID", "Nickname", "Account Topic"]

            for account in cached_accounts:
                table.add_row([cached_accounts.index(account) + 1, colored(account["id"], "cyan"), colored(account["nickname"], "blue"), colored(account["topic"], "green")])

            print(table)
            info("Type 'd' to delete an account.", False)

            user_input = question("Select an account to start (or 'd' to delete): ").strip()

            if user_input.lower() == "d":
                delete_input = question("Enter account number to delete: ").strip()
                account_to_delete = None

                for account in cached_accounts:
                    if str(cached_accounts.index(account) + 1) == delete_input:
                        account_to_delete = account
                        break

                if account_to_delete is None:
                    error("Invalid account selected. Please try again.", "red")
                else:
                    confirm = question(f"Are you sure you want to delete '{account_to_delete['nickname']}'? (Yes/No): ").strip().lower()

                    if confirm == "yes":
                        remove_account("twitter", account_to_delete["id"])
                        success("Account removed successfully!")
                    else:
                        warning("Account deletion canceled.", False)

                return

            selected_account = None

            for account in cached_accounts:
                if str(cached_accounts.index(account) + 1) == user_input:
                    selected_account = account

            if selected_account is None:
                error("Invalid account selected. Please try again.", "red")
                main()
            else:
                if not selected_account.get("character_context", "").strip():
                    warning(
                        "This Twitter account has no character/context yet. Adding one will keep future posts more consistent."
                    )
                    character_context = question(
                        " => Enter the account character/context (or leave empty to skip): "
                    ).strip()
                    if character_context:
                        update_account(
                            "twitter",
                            selected_account["id"],
                            {"character_context": character_context},
                        )
                        selected_account["character_context"] = character_context
                        success("Saved account character/context.")

                twitter = Twitter(
                    selected_account["id"],
                    selected_account["nickname"],
                    selected_account["firefox_profile"],
                    selected_account["topic"],
                    selected_account.get("character_context", ""),
                )

                while True:
                    
                    info("\n============ OPTIONS ============", False)

                    for idx, twitter_option in enumerate(TWITTER_OPTIONS):
                        print(colored(f" {idx + 1}. {twitter_option}", "cyan"))

                    info("=================================\n", False)

                    # Get user input
                    user_input = int(question("Select an option: "))

                    if user_input == 1:
                        twitter.post()
                    elif user_input == 2:
                        posts = twitter.get_posts()

                        posts_table = PrettyTable()

                        posts_table.field_names = ["ID", "Date", "Content"]

                        for post in posts:
                            posts_table.add_row([
                                posts.index(post) + 1,
                                colored(post["date"], "blue"),
                                colored(post["content"][:60] + "...", "green")
                            ])

                        print(posts_table)
                    elif user_input == 3:
                        info("How often do you want to post?")

                        info("\n============ OPTIONS ============", False)
                        for idx, cron_option in enumerate(TWITTER_CRON_OPTIONS):
                            print(colored(f" {idx + 1}. {cron_option}", "cyan"))

                        info("=================================\n", False)

                        user_input = int(question("Select an Option: "))

                        cron_script_path = os.path.join(ROOT_DIR, "src", "cron.py")
                        command = [
                            "python",
                            cron_script_path,
                            "twitter",
                            selected_account['id'],
                            get_active_model(),
                            get_active_provider(),
                        ]

                        def job():
                            subprocess.run(command)

                        if user_input == 1:
                            # Post Once a day
                            schedule.every(1).day.do(job)
                            success("Set up CRON Job.")
                        elif user_input == 2:
                            # Post twice a day
                            schedule.every().day.at("10:00").do(job)
                            schedule.every().day.at("16:00").do(job)
                            success("Set up CRON Job.")
                        elif user_input == 3:
                            # Post thrice a day
                            schedule.every().day.at("08:00").do(job)
                            schedule.every().day.at("12:00").do(job)
                            schedule.every().day.at("18:00").do(job)
                            success("Set up CRON Job.")
                        else:
                            break
                    elif user_input == 4:
                        if get_verbose():
                            info(" => Climbing Options Ladder...", False)
                        break
    elif user_input == 3:
        info("Starting TikTok Service...")

        cached_accounts = get_accounts("tiktok")

        if len(cached_accounts) == 0:
            warning("No TikTok accounts found in cache. Create one now?")
            user_input = question("Yes/No: ")

            if user_input.lower() == "yes":
                generated_uuid = str(uuid4())

                success(f" => Generated ID: {generated_uuid}")
                nickname = question(" => Enter a nickname for this account: ")
                fp_profile = question(" => Enter the path to the Firefox profile: ")
                niche = question(" => Enter the account niche: ")
                language = question(" => Enter the account language: ")
                dialect = question(" => Enter the account dialect/style (or leave empty): ").strip()
                character_context = question(
                    " => Enter the account character/context (tone, personality, audience): "
                )

                add_account("tiktok", {
                    "id": generated_uuid,
                    "nickname": nickname,
                    "firefox_profile": fp_profile,
                    "niche": niche,
                    "language": language,
                    "dialect": dialect,
                    "character_context": character_context,
                    "videos": [],
                })

                success("Account configured successfully!")
            else:
                return

        table = PrettyTable()
        table.field_names = ["ID", "UUID", "Nickname", "Niche"]

        cached_accounts = get_accounts("tiktok")
        for account in cached_accounts:
            table.add_row([
                cached_accounts.index(account) + 1,
                colored(account["id"], "cyan"),
                colored(account["nickname"], "blue"),
                colored(account["niche"], "green"),
            ])

        print(table)
        info("Type 'd' to delete an account.", False)

        user_input = question("Select an account to start (or 'd' to delete): ").strip()

        if user_input.lower() == "d":
            delete_input = question("Enter account number to delete: ").strip()
            account_to_delete = None

            for account in cached_accounts:
                if str(cached_accounts.index(account) + 1) == delete_input:
                    account_to_delete = account
                    break

            if account_to_delete is None:
                error("Invalid account selected. Please try again.", "red")
            else:
                confirm = question(f"Are you sure you want to delete '{account_to_delete['nickname']}'? (Yes/No): ").strip().lower()

                if confirm == "yes":
                    remove_account("tiktok", account_to_delete["id"])
                    success("Account removed successfully!")
                else:
                    warning("Account deletion canceled.", False)

            return

        selected_account = None

        for account in cached_accounts:
            if str(cached_accounts.index(account) + 1) == user_input:
                selected_account = account

        if selected_account is None:
            error("Invalid account selected. Please try again.", "red")
            main()
        else:
            if not selected_account.get("character_context", "").strip():
                warning(
                    "This TikTok account has no character/context yet. Adding one will keep future captions more consistent."
                )
                character_context = question(
                    " => Enter the account character/context (or leave empty to skip): "
                ).strip()
                if character_context:
                    update_account(
                        "tiktok",
                        selected_account["id"],
                        {"character_context": character_context},
                    )
                    selected_account["character_context"] = character_context
                    success("Saved account character/context.")

            tiktok = TikTok(
                selected_account["id"],
                selected_account["nickname"],
                selected_account["firefox_profile"],
                selected_account["niche"],
                selected_account["language"],
                selected_account.get("dialect", ""),
                selected_account.get("character_context", ""),
                open_browser=False,
            )

            while True:
                info("\n============ OPTIONS ============", False)

                for idx, tiktok_option in enumerate(TIKTOK_OPTIONS):
                    print(colored(f" {idx + 1}. {tiktok_option}", "cyan"))

                info("=================================\n", False)

                user_input = int(question("Select an option: "))

                if user_input == 1:
                    drafts = load_youtube_drafts()
                    video_path = ""
                    metadata = {}

                    if drafts:
                        drafts_table = PrettyTable()
                        drafts_table.field_names = ["ID", "Created", "Title"]

                        for draft in drafts[:10]:
                            title = str(draft.get("metadata", {}).get("title", "(untitled)"))[:60]
                            drafts_table.add_row([
                                drafts.index(draft) + 1,
                                colored(str(draft.get("created_at", "")).replace("T", " "), "blue"),
                                colored(title, "green"),
                            ])

                        print(drafts_table)
                        draft_choice = question(
                            "Select a YouTube draft to reuse or type 'm' for a manual MP4 path: "
                        ).strip().lower()

                        if draft_choice != "m":
                            for draft in drafts[:10]:
                                if str(drafts.index(draft) + 1) == draft_choice:
                                    video_path = draft.get("video_path", "")
                                    metadata = draft.get("metadata", {}) or {}
                                    break

                    if not video_path:
                        video_path = question(" => Enter the path to the MP4 file: ").strip()

                    video_path = os.path.abspath(video_path)
                    if not os.path.exists(video_path):
                        error("The selected MP4 file does not exist.", "red")
                        continue

                    default_caption = tiktok.build_basic_caption(metadata)
                    if default_caption:
                        info(f"Suggested caption:\n{default_caption}", False)

                    refine_caption = "no"
                    if metadata:
                        refine_caption = question(
                            "Do you want to refine the caption with AI? (Yes/No): "
                        ).strip().lower()

                    if metadata and refine_caption == "yes":
                        caption = tiktok.generate_caption(metadata)
                        info(f"Generated TikTok caption:\n{caption}", False)
                    else:
                        caption = question(
                            " => Enter the TikTok caption (leave empty to use the suggested one): "
                        ).strip()
                        if not caption:
                            caption = default_caption

                    uploader = TikTok(
                        selected_account["id"],
                        selected_account["nickname"],
                        selected_account["firefox_profile"],
                        selected_account["niche"],
                        selected_account["language"],
                        selected_account.get("dialect", ""),
                        selected_account.get("character_context", ""),
                        open_browser=True,
                    )
                    uploader.upload_video(video_path, caption)
                elif user_input == 2:
                    videos = tiktok.get_videos()

                    if len(videos) > 0:
                        videos_table = PrettyTable()
                        videos_table.field_names = ["ID", "Date", "Caption"]

                        for video in videos:
                            videos_table.add_row([
                                videos.index(video) + 1,
                                colored(video["date"], "blue"),
                                colored(video["caption"][:70] + "...", "green") if len(video.get("caption", "")) > 70 else colored(video.get("caption", ""), "green"),
                            ])

                        print(videos_table)
                    else:
                        warning(" No TikTok uploads found.")
                elif user_input == 3:
                    if get_verbose():
                        info(" => Climbing Options Ladder...", False)
                    break
    elif user_input == 4:
        info("Starting Affiliate Marketing...")

        cached_products = get_products()

        if len(cached_products) == 0:
            warning("No products found in cache. Create one now?")
            user_input = question("Yes/No: ")

            if user_input.lower() == "yes":
                affiliate_link = question(" => Enter the affiliate link: ")
                twitter_uuid = question(" => Enter the Twitter Account UUID: ")

                # Find the account
                account = None
                for acc in get_accounts("twitter"):
                    if acc["id"] == twitter_uuid:
                        account = acc

                add_product({
                    "id": str(uuid4()),
                    "affiliate_link": affiliate_link,
                    "twitter_uuid": twitter_uuid
                })

                afm = AffiliateMarketing(affiliate_link, account["firefox_profile"], account["id"], account["nickname"], account["topic"])

                afm.generate_pitch()
                afm.share_pitch("twitter")
        else:
            table = PrettyTable()
            table.field_names = ["ID", "Affiliate Link", "Twitter Account UUID"]

            for product in cached_products:
                table.add_row([cached_products.index(product) + 1, colored(product["affiliate_link"], "cyan"), colored(product["twitter_uuid"], "blue")])

            print(table)

            user_input = question("Select a product to start: ")

            selected_product = None

            for product in cached_products:
                if str(cached_products.index(product) + 1) == user_input:
                    selected_product = product

            if selected_product is None:
                error("Invalid product selected. Please try again.", "red")
                main()
            else:
                # Find the account
                account = None
                for acc in get_accounts("twitter"):
                    if acc["id"] == selected_product["twitter_uuid"]:
                        account = acc

                afm = AffiliateMarketing(selected_product["affiliate_link"], account["firefox_profile"], account["id"], account["nickname"], account["topic"])

                afm.generate_pitch()
                afm.share_pitch("twitter")

    elif user_input == 5:
        info("Starting Outreach...")

        outreach = Outreach()

        outreach.start()
    elif user_input == 6:
        if get_verbose():
            print(colored(" => Quitting...", "blue"))
        sys.exit(0)
    else:
        error("Invalid option selected. Please try again.", "red")
        main()
    

if __name__ == "__main__":
    # Print ASCII Banner
    print_banner()

    first_time = get_first_time_running()

    if first_time:
        print(colored("Hey! It looks like you're running MoneyPrinter V2 for the first time. Let's get you setup first!", "yellow"))

    # Setup file tree
    assert_folder_structure()

    # Remove temporary files
    rem_temp_files()

    # Fetch MP3 Files
    fetch_songs()

    provider = get_llm_provider()
    configured_model = get_configured_llm_model()

    if provider not in ("ollama", "openai"):
        error(f"Unsupported llm_provider '{provider}'. Use 'ollama' or 'openai'.")
        sys.exit(1)

    if provider == "openai" and not get_openai_api_key():
        error("OpenAI provider selected, but no API key was found. Set openai_api_key in config.json or OPENAI_API_KEY in your environment.")
        sys.exit(1)

    if configured_model:
        select_model(configured_model, provider=provider)
        success(f"Using configured {provider} model: {configured_model}")
    elif provider == "openai":
        default_openai_model = "gpt-5-mini"
        select_model(default_openai_model, provider=provider)
        success(
            f"Using default OpenAI model: {default_openai_model}. Set openai_model in config.json to change it."
        )
    else:
        try:
            models = list_models(provider=provider)
        except Exception as e:
            error(f"Could not connect to Ollama: {e}")
            sys.exit(1)

        if not models:
            error("No models found on Ollama. Pull a model first (e.g. 'ollama pull llama3.2:3b').")
            sys.exit(1)

        info("\n========== OLLAMA MODELS =========", False)
        for idx, model_name in enumerate(models):
            print(colored(f" {idx + 1}. {model_name}", "cyan"))
        info("==================================\n", False)

        model_choice = None
        while model_choice is None:
            raw = input(colored("Select a model: ", "magenta")).strip()
            try:
                choice_idx = int(raw) - 1
                if 0 <= choice_idx < len(models):
                    model_choice = models[choice_idx]
                else:
                    warning("Invalid selection. Try again.")
            except ValueError:
                warning("Please enter a number.")

        select_model(model_choice, provider=provider)
        success(f"Using model: {model_choice}")

    while True:
        main()
