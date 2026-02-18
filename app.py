"""
app.py — Gradio UI for the Facebook Group Scraper.

Run with:
    python app.py
Then open http://localhost:7860 in your browser.

Architecture:
  - Scraping runs in a background thread (ThreadPoolExecutor) with its own
    asyncio event loop, so it never conflicts with Gradio's event loop.
  - Log messages flow through a queue.Queue for live streaming to the UI.
  - The Gradio generator polls the queue and yields updates incrementally.
"""

import json
import os
import queue
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gradio as gr
import pandas as pd
from dotenv import load_dotenv

from analyzer import analyze_posts
from scraper import scrape_group_threaded, COOKIES_FILE

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CRITERIA = (
    "z czym ludzie mają największe zmagania, "
    "jakiej szukają pomocy, "
    "z jakimi problemami mierzą się na codzień"
)

_executor = ThreadPoolExecutor(max_workers=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_custom_keywords(raw: str) -> list[str]:
    return [kw.strip() for kw in raw.split(",") if kw.strip()]


def _session_status() -> str:
    if COOKIES_FILE.exists():
        return "✅ Zapisana sesja istnieje"
    return "ℹ️ Brak zapisanej sesji"


# ---------------------------------------------------------------------------
# Group history (groups_history.json)
# ---------------------------------------------------------------------------

GROUPS_HISTORY_FILE = Path("groups_history.json")


def _load_history() -> list[dict]:
    """Return list of {name, url} dicts, newest first."""
    if not GROUPS_HISTORY_FILE.exists():
        return []
    try:
        return json.loads(GROUPS_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_to_history(url: str) -> None:
    """Derive group name from URL and prepend to history (no duplicates)."""
    url = url.strip().rstrip("/")
    # Derive a human-readable name from the URL slug
    slug = url.split("/groups/")[-1].split("/")[0] if "/groups/" in url else url.split("/")[-1]
    name = slug.replace("-", " ").replace("_", " ").title() or url
    history = _load_history()
    # Remove existing entry for same URL
    history = [h for h in history if h["url"] != url]
    history.insert(0, {"name": name, "url": url})
    # Keep at most 20 entries
    history = history[:20]
    GROUPS_HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _history_choices() -> list[str]:
    """Return display strings for the dropdown."""
    return [f"{h['name']} — {h['url']}" for h in _load_history()]


def _url_from_choice(choice: str) -> str:
    """Extract URL from a dropdown choice string."""
    if " — " in choice:
        return choice.split(" — ", 1)[1]
    return choice


# ---------------------------------------------------------------------------
# Presets history (presets.json) — for criteria and keywords
# ---------------------------------------------------------------------------

PRESETS_FILE = Path("presets.json")


def _load_presets(key: str) -> list[str]:
    """Return saved preset strings for a given key (e.g. 'criteria', 'keywords')."""
    if not PRESETS_FILE.exists():
        return []
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        return data.get(key, [])
    except Exception:
        return []


def _save_preset(key: str, value: str) -> None:
    """Prepend value to presets[key], deduplicate, keep at most 15."""
    value = value.strip()
    if not value:
        return
    data: dict = {}
    if PRESETS_FILE.exists():
        try:
            data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing = data.get(key, [])
    existing = [v for v in existing if v != value]  # remove duplicate
    existing.insert(0, value)
    data[key] = existing[:15]
    PRESETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

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
):
    """
    Gradio generator: yields (log_text, results_df, export_btn_update) tuples.
    The scraper runs in a background thread; we poll its log queue here.
    """
    # --- Validate ---
    if not group_url.strip():
        yield "❌ Proszę podać URL grupy Facebook.", None, gr.update(visible=False)
        return
    if not email.strip() and not COOKIES_FILE.exists():
        yield "❌ Proszę podać adres e-mail.", None, gr.update(visible=False)
        return
    if not password.strip() and not COOKIES_FILE.exists():
        yield "❌ Proszę podać hasło.", None, gr.update(visible=False)
        return

    # Save group to history before scraping
    if group_url.strip():
        _save_to_history(group_url.strip())

    # Save criteria and keywords presets
    if criteria_description.strip():
        _save_preset("criteria", criteria_description.strip())
    if custom_keywords_raw.strip():
        _save_preset("keywords", custom_keywords_raw.strip())

    custom_keywords = _parse_custom_keywords(custom_keywords_raw)
    if not gemini_api_key.strip():
        gemini_api_key = os.getenv("GEMINI_API_KEY", "")

    log_lines: list[str] = []
    log_q: queue.Queue[str | None] = queue.Queue()
    result_holder: list[list[dict]] = [[]]  # mutable container for thread result

    def _run_scraper():
        posts = scrape_group_threaded(
            group_url=group_url.strip(),
            email=email.strip(),
            password=password.strip(),
            max_posts=int(max_posts),
            save_session=save_session,
            headless=headless,
            log_queue=log_q,
            scroll_wait_ms=int(scroll_wait_ms),
            per_post_timeout=float(per_post_timeout),
            enrich_total_timeout=float(enrich_total_timeout),
        )
        result_holder[0] = posts

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


def clear_session():
    if COOKIES_FILE.exists():
        COOKIES_FILE.unlink()
        return "🗑️ Sesja usunięta."
    return "ℹ️ Brak zapisanej sesji."


# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

CUSTOM_CSS = """
/* ── Light mode defaults ── */
:root {
    --primary: #1877f2;
    --primary-dark: #0d5dbf;
    --bg: #f0f2f5;
    --card-bg: #ffffff;
    --text: #1c1e21;
    --muted: #65676b;
    --border: #dddfe2;
    --radius: 12px;
    --shadow: 0 2px 12px rgba(0,0,0,0.10);
    --log-bg: #1a1a2e;
    --log-text: #e0e0e0;
    --table-stripe: #f7f8fa;
    --section-title-color: #65676b;
}

/* ── Dark mode — Gradio adds .dark to <body> ── */
.dark {
    --bg: #0f1117;
    --card-bg: #1a1d27;
    --text: #e4e6eb;
    --muted: #9a9da5;
    --border: #2d3040;
    --shadow: 0 2px 16px rgba(0,0,0,0.40);
    --table-stripe: #1f2233;
    --section-title-color: #9a9da5;
}

/* ── Also respect system preference when Gradio hasn't set .dark ── */
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #0f1117;
        --card-bg: #1a1d27;
        --text: #e4e6eb;
        --muted: #9a9da5;
        --border: #2d3040;
        --shadow: 0 2px 16px rgba(0,0,0,0.40);
        --table-stripe: #1f2233;
        --section-title-color: #9a9da5;
    }
}

/* ── Base ── */
body,
.gradio-container,
.gradio-container > .main,
footer {
    background: var(--bg) !important;
    color: var(--text) !important;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif !important;
}

.gradio-container {
    max-width: 1100px !important;
    margin: 0 auto !important;
}

/* ── Header ── */
.app-header {
    background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
    border-radius: var(--radius);
    padding: 28px 32px 22px;
    margin-bottom: 20px;
    box-shadow: var(--shadow);
    color: white;
    text-align: center;
}
.app-header h1 {
    font-size: 2rem;
    font-weight: 800;
    margin: 0 0 6px;
    letter-spacing: -0.5px;
}
.app-header p {
    font-size: 0.95rem;
    opacity: 0.88;
    margin: 0;
}

/* ── Section titles ── */
.section-title {
    font-size: 0.8rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--section-title-color);
    margin-bottom: 12px;
}

/* ── Gradio panels / blocks ── */
.gr-panel, .gr-box, .block, .form {
    background: var(--card-bg) !important;
    border-color: var(--border) !important;
}

/* ── Log area ── */
.log-area textarea {
    font-family: 'SF Mono', 'Fira Code', monospace !important;
    font-size: 0.82rem !important;
    background: var(--log-bg) !important;
    color: var(--log-text) !important;
    border-radius: 8px !important;
    border: none !important;
}

/* ── Results table ── */
.results-table table { border-collapse: collapse; width: 100%; }
.results-table th {
    background: var(--primary) !important;
    color: white !important;
    font-weight: 700 !important;
    padding: 10px 14px !important;
    text-align: left !important;
}
.results-table td {
    padding: 10px 14px !important;
    border-bottom: 1px solid var(--border) !important;
    vertical-align: top !important;
    color: var(--text) !important;
    background: var(--card-bg) !important;
}
.results-table tr:nth-child(even) td {
    background: var(--table-stripe) !important;
}
"""


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

with gr.Blocks(title="📊 Facebook Group Scraper") as demo:

    gr.HTML("""
    <div class="app-header">
        <h1>📊 Facebook Group Scraper</h1>
        <p>Znajdź najczęstsze pytania i problemy w grupach na Facebooku • Analiza po polsku</p>
    </div>
    """)

    with gr.Tabs() as tabs:

        # ── Tab 1: Configuration ────────────────────────────────────────────
        with gr.Tab("⚙️ Konfiguracja", id="config"):

            with gr.Row():
                with gr.Column(scale=3):
                    gr.HTML('<div class="section-title">🔗 Grupa Facebook</div>')
                    with gr.Row():
                        group_url = gr.Textbox(
                            label="URL grupy",
                            placeholder="https://www.facebook.com/groups/nazwa-grupy",
                            info="Wklej pełny link do grupy Facebook",
                            scale=4,
                        )
                    with gr.Row():
                        history_dropdown = gr.Dropdown(
                            label="📂 Ostatnie grupy",
                            choices=_history_choices(),
                            value=None,
                            interactive=True,
                            info="Wybierz grupę z historii, aby wczytać URL",
                        )
                with gr.Column(scale=2):
                    gr.HTML('<div class="section-title">📈 Parametry</div>')
                    max_posts = gr.Slider(
                        label="Maksymalna liczba postów do pobrania",
                        minimum=20, maximum=500, value=100, step=10,
                    )
                    top_n = gr.Slider(
                        label="Liczba wyników do wyświetlenia",
                        minimum=5, maximum=50, value=20, step=1,
                    )

            gr.HTML('<div class="section-title">🔐 Dane logowania</div>')
            with gr.Row():
                email = gr.Textbox(
                    label="E-mail Facebook",
                    placeholder="twoj@email.com",
                    scale=2,
                )
                password = gr.Textbox(
                    label="Hasło Facebook",
                    placeholder="••••••••",
                    type="password",
                    scale=2,
                )
                with gr.Column(scale=1):
                    save_session = gr.Checkbox(
                        label="💾 Zapisz sesję",
                        value=True,
                        info="Zapisuje ciasteczka, aby pominąć logowanie następnym razem",
                    )
                    session_status_md = gr.Markdown(value=_session_status())
                    clear_session_btn = gr.Button("🗑️ Usuń sesję", size="sm", variant="secondary")

            gr.HTML('<div class="section-title">🔍 Kryteria wyszukiwania</div>')
            with gr.Row():
                with gr.Column():
                    criteria_description = gr.Textbox(
                        label="Opis kryteriów (używany przez Gemini)",
                        value=DEFAULT_CRITERIA,
                        lines=2,
                    )
                    criteria_preset = gr.Dropdown(
                        label="📂 Poprzednie kryteria",
                        choices=_load_presets("criteria"),
                        value=None,
                        interactive=True,
                        info="Wybierz wcześniej użyte kryterium",
                    )
                    custom_keywords = gr.Textbox(
                        label="Dodatkowe słowa kluczowe (oddzielone przecinkami)",
                        placeholder="np. dieta, trening, motywacja, schudnąć",
                        info="Posty zawierające te słowa będą zawsze uwzględnione",
                    )
                    keywords_preset = gr.Dropdown(
                        label="📂 Poprzednie słowa kluczowe",
                        choices=_load_presets("keywords"),
                        value=None,
                        interactive=True,
                        info="Wybierz wcześniej użyte słowa kluczowe",
                    )

            gr.HTML('<div class="section-title">🤖 Gemini AI</div>')
            with gr.Row():
                gemini_api_key = gr.Textbox(
                    label="Klucz API Gemini",
                    placeholder="AIza... (lub ustaw GEMINI_API_KEY w pliku .env)",
                    type="password",
                    info="Bezpłatny klucz: https://aistudio.google.com/app/apikey — wymagany do analizy semantycznej",
                    scale=4,
                )
                headless = gr.Checkbox(
                    label="Tryb bez okna (headless)",
                    value=True,
                    info="Ukrywa przeglądarkę. Wyłącz jeśli masz 2FA.",
                    scale=1,
                )

            gr.HTML('<div class="section-title">⏱️ Limity czasowe</div>')
            with gr.Row():
                scroll_wait_ms = gr.Slider(
                    label="Oczekiwanie po przewinięciu (ms)",
                    minimum=500, maximum=5000, value=1500, step=250,
                    info="Czas oczekiwania po każdym przewinięciu strony. Więcej = wolniej, ale pewniej.",
                )
                per_post_timeout = gr.Slider(
                    label="Limit czasu na post (s)",
                    minimum=1, maximum=30, value=5, step=1,
                    info="Maks. czas wzbogacania jednego posta (reakcje, komentarze).",
                )
                enrich_total_timeout = gr.Slider(
                    label="Limit czasu wzbogacania łącznie (s)",
                    minimum=10, maximum=300, value=60, step=10,
                    info="Maks. łączny czas fazy wzbogacania. Po przekroczeniu — reszta bez danych.",
                )

            start_btn = gr.Button(
                "🚀 Rozpocznij scrapowanie",
                variant="primary",
                size="lg",
            )

        # ── Tab 2: Results ──────────────────────────────────────────────────
        with gr.Tab("📊 Wyniki", id="results"):

            log_output = gr.Textbox(
                label="📋 Log postępu",
                lines=12,
                interactive=False,
                elem_classes="log-area",
            )

            results_table = gr.Dataframe(
                label="🏆 Najczęstsze pytania i problemy",
                interactive=False,
                wrap=True,
                elem_classes="results-table",
            )

            with gr.Row():
                export_btn = gr.DownloadButton(
                    label="📥 Pobierz CSV",
                    variant="secondary",
                    size="sm",
                    visible=False,
                )
                gr.HTML(
                    '<p style="color:#65676b;font-size:0.82rem;margin-top:8px;">'
                    "Wyniki posortowane według zaangażowania (reakcje + komentarze×3). "
                    "Podsumowania i kategorie zawsze po polsku."
                    "</p>"
                )

    # ── Events ───────────────────────────────────────────────────────────────

    clear_session_btn.click(fn=clear_session, outputs=session_status_md)

    # Load URL from history dropdown
    history_dropdown.change(
        fn=_url_from_choice,
        inputs=history_dropdown,
        outputs=group_url,
    )

    # Load criteria / keywords from presets
    criteria_preset.change(
        fn=lambda v: v,
        inputs=criteria_preset,
        outputs=criteria_description,
    )
    keywords_preset.change(
        fn=lambda v: v,
        inputs=keywords_preset,
        outputs=custom_keywords,
    )

    # Switch to results tab immediately, then run pipeline
    start_btn.click(
        fn=lambda: gr.Tabs(selected="results"),
        outputs=tabs,
    ).then(
        fn=run_pipeline,
        inputs=[
            group_url, email, password, max_posts, save_session,
            gemini_api_key, criteria_description,
            custom_keywords, top_n, headless,
            scroll_wait_ms, per_post_timeout, enrich_total_timeout,
        ],
        outputs=[log_output, results_table, export_btn],
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        inbrowser=True,
        css=CUSTOM_CSS,
        theme=gr.themes.Soft(
            primary_hue="blue",
            secondary_hue="slate",
            neutral_hue="slate",
            font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui"],
        ),
    )
