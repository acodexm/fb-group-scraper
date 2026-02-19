import os
import queue
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import gradio as gr
import pandas as pd

from analyzer import analyze_posts
from scraper import scrape_group_threaded
from app.persistence import (
    save_to_history,
    save_preset,
    DEFAULT_CRITERIA,
    load_history,
    load_presets,
    get_session_file_path,
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

    # --- Validate ---
    if not group_url.strip():
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

    custom_keywords = parse_custom_keywords(custom_keywords_raw)
    if not gemini_api_key.strip():
        gemini_api_key = os.getenv("GEMINI_API_KEY", "")

    log_lines: list[str] = []
    log_q: queue.Queue[str | None] = queue.Queue()
    result_holder: list[list[dict]] = [[]]  # mutable container for thread result

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
        if group_name:
            save_to_history(group_url.strip(), group_name)

    # --- Launch scraper in background thread ---
    log_lines.append("🚀 Rozpoczynam scrapowanie...")
    yield "\n".join(log_lines), None, gr.update(visible=False)

    future = _executor.submit(_run_scraper)

    # --- Stream logs while scraper runs ---
    while True:
        try:
            msg = log_q.get(timeout=0.3)
        except queue.Empty:
            # Yield current log state to keep UI alive
            yield "\n".join(log_lines), None, gr.update(visible=False)
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
        yield "\n".join(log_lines), None, gr.update(visible=False)

    # Check for exceptions in the scraper thread
    try:
        future.result()
    except Exception as e:
        log_lines.append(f"❌ Błąd podczas scrapowania: {e}")
        yield "\n".join(log_lines), None, gr.update(visible=False)
        return

    posts = result_holder[0]

    if not posts:
        log_lines.append("⚠️ Nie znaleziono żadnych postów. Sprawdź URL grupy i dane logowania.")
        yield "\n".join(log_lines), None, gr.update(visible=False)
        return

    # --- Analysis ---
    log_lines.append(f"\n📊 Analizuję {len(posts)} postów...")
    yield "\n".join(log_lines), None, gr.update(visible=False)

    analysis_log: list[str] = []

    def analysis_log_fn(msg: str):
        analysis_log.append(msg)

    try:
        df = analyze_posts(
            posts=posts,
            custom_keywords=custom_keywords,
            top_n=int(top_n),
            gemini_api_key=gemini_api_key,
            criteria_description=criteria_description or DEFAULT_CRITERIA,
            model=model,
            log=analysis_log_fn,
        )
    except Exception as e:
        log_lines.append(f"❌ Błąd podczas analizy: {e}")
        yield "\n".join(log_lines), None, gr.update(visible=False)
        return

    log_lines.extend(analysis_log)
    yield "\n".join(log_lines), None, gr.update(visible=False)

    if df is None or df.empty:
        log_lines.append("⚠️ Brak wyników spełniających kryteria.")
        yield "\n".join(log_lines), None, gr.update(visible=False)
        return

    # --- Export ---
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".csv", prefix="fb_scraper_results_"
    )
    df.to_csv(tmp.name, index=False, encoding="utf-8-sig")

    display_df = df[["rank", "original_question", "summary", "category", "reactions", "comments"]].copy()
    display_df.columns = ["#", "Oryginalne pytanie", "Podsumowanie (PL)", "Kategoria", "Reakcje", "Komentarze"]
    # Sanitize newlines in text columns — multi-line cells break Gradio's table rendering
    for col in ["Oryginalne pytanie", "Podsumowanie (PL)", "Kategoria"]:
        display_df[col] = display_df[col].astype(str).str.replace(r'[\r\n]+', ' ', regex=True).str.strip()

    log_lines.append(f"\n🎉 Gotowe! Znaleziono {len(df)} pytań/problemów.")
    yield "\n".join(log_lines), display_df, gr.update(value=tmp.name, visible=True)


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
