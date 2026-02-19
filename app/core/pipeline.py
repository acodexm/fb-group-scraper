import os
import queue
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import gradio as gr
import pandas as pd

from analyzer import process_and_summarize
from scraper import scrape_group_threaded
from app.persistence import (
    save_to_history,
    save_preset,
    DEFAULT_CRITERIA,
    load_history,
    load_presets,
    get_session_file_path,
    save_run,
)

_executor = ThreadPoolExecutor(max_workers=1)
STOP_EVENT = threading.Event()


def parse_custom_keywords(raw: str) -> list[str]:
    return [kw.strip() for kw in raw.split(",") if kw.strip()]


def stop_scraper():
    """Signal the scraper to stop."""
    STOP_EVENT.set()
    return "🛑 Sygnał zatrzymania wysłany..."


def run_pipeline(
    group_url: str,
    email: str,
    password: str,
    max_posts: int,
    save_session: bool,
    gemini_api_key: str,
    criteria_description: str,
    custom_keywords_raw: str,
    top_n: int,
    headless: bool,
    scroll_wait_ms: int,
    per_post_timeout: float,
    enrich_total_timeout: float,
    model: str,
):
    """
    Gradio generator: yields (log_text, results_df, export_btn_update) tuples.
    The scraper runs in a background thread; we poll its log queue here.
    """
    STOP_EVENT.clear()

    # --- Sanitization ---
    group_url = (group_url or "").strip()
    email = (email or "").strip()
    password = (password or "").strip()
    gemini_api_key = (gemini_api_key or "").strip()
    criteria_description = (criteria_description or "").strip()
    custom_keywords_raw = (custom_keywords_raw or "").strip()
    # top_n and max_posts are ints, likely not None ifSlider used, but good to check?
    # Sliders usually pass int.

    # --- Validate ---
    if not group_url:
        yield "❌ Proszę podać URL grupy Facebook.", None, gr.update(visible=False)
        return

    input_email = email.strip()
    session_file_path = get_session_file_path(input_email)

    if not input_email and not session_file_path.exists():
        yield "❌ Proszę podać adres e-mail (lub upewnij się, że masz zapisaną sesję dla pustego emaila).", None, gr.update(visible=False)
        return
    if not password.strip() and not session_file_path.exists():
        yield "❌ Proszę podać hasło (lub upewnij się, że masz zapisaną sesję).", None, gr.update(visible=False)
        return

    # Save group to history before scraping
    if group_url.strip():
        save_to_history(group_url.strip())

    # Save criteria and keywords presets
    if criteria_description.strip():
        save_preset("criteria", criteria_description.strip())
    if custom_keywords_raw.strip():
        save_preset("keywords", custom_keywords_raw.strip())
    
    if not gemini_api_key.strip():
        gemini_api_key = os.getenv("GEMINI_API_KEY", "")

    log_lines: list[str] = []
    log_q: queue.Queue[str | None] = queue.Queue()
    # Container for scraper result: [posts, group_name]
    result_holder: list[object] = [[], ""] 

    def _run_scraper():
        if STOP_EVENT.is_set():
            return

        posts, group_name = scrape_group_threaded(
            group_url=group_url.strip(),
            email=input_email,
            password=password.strip(),
            max_posts=int(max_posts),
            save_session=save_session,
            headless=headless,
            session_file_path=session_file_path,
            log_queue=log_q,
            scroll_wait_ms=int(scroll_wait_ms),
            per_post_timeout=float(per_post_timeout),
            enrich_total_timeout=float(enrich_total_timeout),
            stop_event=STOP_EVENT,
        )
        result_holder[0] = posts
        result_holder[1] = group_name
        
        if group_name:
            save_to_history(group_url.strip(), group_name)

    # --- Launch scraper in background thread ---
    log_lines.append("🚀 Rozpoczynam scrapowanie...")
    yield "\n".join(log_lines), "", gr.update(visible=False)

    future = _executor.submit(_run_scraper)

    # --- Stream logs while scraper runs ---
    while True:
        try:
            msg = log_q.get(timeout=0.3)
        except queue.Empty:
            # Yield current log state to keep UI alive
            yield "\n".join(log_lines), "", gr.update(visible=False)
            if future.done():
                # Drain any remaining messages
                while not log_q.empty():
                    msg = log_q.get_nowait()
                    if msg is None:
                        break
                    log_lines.append(msg)
                break
            continue

        if msg is None:
            # Sentinel: scraping finished
            break
        log_lines.append(msg)
        yield "\n".join(log_lines), "", gr.update(visible=False)

    # Check for exceptions in the scraper thread
    try:
        future.result()
    except Exception as e:
        log_lines.append(f"❌ Błąd podczas scrapowania: {e}")
        yield "\n".join(log_lines), "", gr.update(visible=False)
        return

    posts = result_holder[0]
    group_name = result_holder[1]

    if not posts:
        log_lines.append("⚠️ Nie znaleziono żadnych postów. Sprawdź URL grupy i dane logowania.")
        yield "\n".join(log_lines), "", gr.update(visible=False)
        return

    # --- Analysis / Summarization ---
    log_lines.append(f"\n📊 Przetwarzam {len(posts)} postów...")
    yield "\n".join(log_lines), "", gr.update(visible=False)

    analysis_log: list[str] = []

    def analysis_log_fn(msg: str):
        analysis_log.append(msg)

    # Call the new processing function
    
    try:
        summary_md, df = process_and_summarize(
            posts=posts,
            user_instructions=criteria_description or DEFAULT_CRITERIA,
            gemini_api_key=gemini_api_key,
            model=model,
            log=analysis_log_fn,
        )
    except Exception as e:
        log_lines.append(f"❌ Błąd podczas analizy: {e}")
        yield "\n".join(log_lines), "", gr.update(visible=False)
        return

    log_lines.extend(analysis_log)
    yield "\n".join(log_lines), "", gr.update(visible=False)

    if not summary_md and df.empty:
        log_lines.append("⚠️ Brak wyników.")
        yield "\n".join(log_lines), "", gr.update(visible=False)
        return
    
    # --- Save Run History ---
    if summary_md:
        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        # Ensure group name is set (fallback to URL slug if empty from scraper)
        final_group_name = group_name if group_name else group_url.split("/")[-1]
        save_run(final_group_name, group_url, summary_md, now_str)
        log_lines.append("💾 Wynik zapisany w historii.")

    # --- Export ---
    tmp_path = ""
    if not df.empty:
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".csv", prefix="fb_scraper_results_"
        )
        df.to_csv(tmp.name, index=False, encoding="utf-8-sig")
        tmp_path = tmp.name

    log_lines.append(f"\n🎉 Gotowe! Raport wygenerowany.")
    yield "\n".join(log_lines), summary_md, gr.update(value=tmp_path, visible=True)


def clear_session(email: str) -> str:
    """Remove the session file for the given email."""
    path = get_session_file_path(email)
    if path.exists():
        try:
            path.unlink()
            return "🗑️ Sesja usunięta."
        except Exception as e:
            return f"⚠️ Błąd usuwania: {e}"
    return "ℹ️ Brak zapisanej sesji."


def session_status(email: str = "") -> str:
    """Check if a session file exists for the given email."""
    # If called without email (e.g. init), we might want to check env/settings?
    # But usually it's called with the input value. 
    # If email is empty, get_session_file_path returns the default/legacy path.
    path = get_session_file_path(email)
    if path.exists():
        return "✅ Zapisana sesja istnieje"
    return "ℹ️ Brak zapisanej sesji"
