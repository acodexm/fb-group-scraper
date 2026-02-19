import gradio as gr
from app.persistence import (
    load_settings,
    history_choices,
    load_presets,
    url_from_choice,
    save_settings,
    save_to_history,
    save_preset,
    DEFAULT_CRITERIA,
)


def create_demo(run_pipeline_fn, clear_session_fn, session_status_fn, stop_scraper_fn):
    """
    Create and return the Gradio Blocks demo.
    Dependencies are injected to avoid circular imports.
    """
    _cfg = load_settings()

    with gr.Blocks(title="📊 Facebook Group Scraper", css=CUSTOM_CSS) as demo:

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
                                value=_cfg["group_url"],
                                scale=4,
                            )
                        with gr.Row():
                            history_dropdown = gr.Dropdown(
                                label="📂 Ostatnie grupy",
                                choices=history_choices(),
                                value=None,
                                interactive=True,
                                info="Wybierz grupę z historii, aby wczytać URL",
                            )
                    with gr.Column(scale=2):
                        gr.HTML('<div class="section-title">📈 Parametry</div>')
                        max_posts = gr.Slider(
                            label="Maksymalna liczba postów do pobrania",
                            minimum=20, maximum=500, value=_cfg["max_posts"], step=10,
                        )
                        top_n = gr.Slider(
                            label="Liczba wyników do wyświetlenia",
                            minimum=5, maximum=50, value=_cfg["top_n"], step=1,
                        )

                gr.HTML('<div class="section-title">🔐 Dane logowania</div>')
                with gr.Row():
                    email = gr.Textbox(
                        label="E-mail Facebook",
                        placeholder="twoj@email.com",
                        value=_cfg["email"],
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
                            value=_cfg["save_session"],
                            info="Zapisuje ciasteczka, aby pominąć logowanie następnym razem",
                        )
                        # Initialize status with loaded email
                        session_status_md = gr.Markdown(value=session_status_fn(_cfg["email"]))
                        clear_session_btn = gr.Button("🗑️ Usuń sesję", size="sm", variant="secondary")

                gr.HTML('<div class="section-title">🔍 Kryteria wyszukiwania</div>')
                with gr.Row():
                    with gr.Column():
                        criteria_description = gr.Textbox(
                            label="Opis kryteriów (używany przez Gemini)",
                            value=_cfg["criteria_description"],
                            lines=2,
                        )
                        criteria_preset = gr.Dropdown(
                            label="📂 Poprzednie kryteria",
                            choices=load_presets("criteria"),
                            value=None,
                            interactive=True,
                            info="Wybierz wcześniej użyte kryterium",
                        )
                        custom_keywords = gr.Textbox(
                            label="Dodatkowe słowa kluczowe (oddzielone przecinkami)",
                            placeholder="np. dieta, trening, motywacja, schudnąć",
                            info="Posty zawierające te słowa będą zawsze uwzględnione",
                            value=_cfg["custom_keywords"],
                        )
                        keywords_preset = gr.Dropdown(
                            label="📂 Poprzednie słowa kluczowe",
                            choices=load_presets("keywords"),
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
                        value=_cfg["gemini_api_key"],
                        scale=3,
                    )
                    model = gr.Dropdown(
                        label="Model Gemini",
                        choices=["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-1.5-flash-8b"],
                        value=_cfg["model"],
                        interactive=True,
                        info="Wybierz model AI do analizy",
                        scale=1,
                    )
                    headless = gr.Checkbox(
                        label="Tryb bez okna (headless)",
                        value=_cfg["headless"],
                        info="Ukrywa przeglądarkę. Wyłącz jeśli masz 2FA.",
                        scale=1,
                    )

                gr.HTML('<div class="section-title">⏱️ Limity czasowe</div>')
                with gr.Row():
                    scroll_wait_ms = gr.Slider(
                        label="Oczekiwanie po przewinięciu (ms)",
                        minimum=500, maximum=5000, value=_cfg["scroll_wait_ms"], step=250,
                        info="Czas oczekiwania po każdym przewinięciu strony. Więcej = wolniej, ale pewniej.",
                    )
                    per_post_timeout = gr.Slider(
                        label="Limit czasu na post (s)",
                        minimum=1, maximum=30, value=_cfg["per_post_timeout"], step=1,
                        info="Maks. czas wzbogacania jednego posta (reakcje, komentarze).",
                    )
                    enrich_total_timeout = gr.Slider(
                        label="Limit czasu wzbogacania łącznie (s)",
                        minimum=10, maximum=300, value=_cfg["enrich_total_timeout"], step=10,
                        info="Maks. łączny czas fazy wzbogacania. Po przekroczeniu — reszta bez danych.",
                    )

                with gr.Row():
                    start_btn = gr.Button(
                        "🚀 Rozpocznij scrapowanie",
                        variant="primary",
                        size="lg",
                        scale=3,
                    )
                    stop_btn = gr.Button(
                        "🛑 Zatrzymaj",
                        variant="stop",
                        size="lg",
                        scale=1,
                    )

            # ── Tab 2: Results ──────────────────────────────────────────────────
            with gr.Tab("📊 Wyniki", id="results"):

                with gr.Row():
                    start_btn_res = gr.Button("🚀 Start", variant="primary", scale=2)
                    stop_btn_res = gr.Button("🛑 Stop", variant="stop", scale=1)

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

        # Pass email to clear_session_fn
        clear_session_btn.click(fn=clear_session_fn, inputs=email, outputs=session_status_md)
        
        # Update status when email changes
        email.change(fn=session_status_fn, inputs=email, outputs=session_status_md)

        # Load URL from history dropdown
        history_dropdown.change(
            fn=url_from_choice,
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

        # ── Auto-save settings on every change ──────────────────────────────
        def _save(key):
            return lambda v: save_settings(**{key: v})

        group_url.change(fn=_save("group_url"), inputs=group_url)
        email.change(fn=_save("email"), inputs=email)
        save_session.change(fn=_save("save_session"), inputs=save_session)
        max_posts.change(fn=_save("max_posts"), inputs=max_posts)
        top_n.change(fn=_save("top_n"), inputs=top_n)
        criteria_description.change(fn=_save("criteria_description"), inputs=criteria_description)
        custom_keywords.change(fn=_save("custom_keywords"), inputs=custom_keywords)
        gemini_api_key.change(fn=_save("gemini_api_key"), inputs=gemini_api_key)
        headless.change(fn=_save("headless"), inputs=headless)
        scroll_wait_ms.change(fn=_save("scroll_wait_ms"), inputs=scroll_wait_ms)
        per_post_timeout.change(fn=_save("per_post_timeout"), inputs=per_post_timeout)
        enrich_total_timeout.change(fn=_save("enrich_total_timeout"), inputs=enrich_total_timeout)
        model.change(fn=_save("model"), inputs=model)

        # Switch to results tab immediately, then run pipeline
        start_btn.click(
            fn=lambda: gr.Tabs(selected="results"),
            outputs=tabs,
        ).then(
            fn=run_pipeline_fn,
            inputs=[
                group_url, email, password, max_posts, save_session,
                gemini_api_key, criteria_description,
                custom_keywords, top_n, headless,
                scroll_wait_ms, per_post_timeout, enrich_total_timeout,
                model,
            ],
            outputs=[log_output, results_table, export_btn],
        )

        start_btn_res.click(
            fn=run_pipeline_fn,
            inputs=[
                group_url, email, password, max_posts, save_session,
                gemini_api_key, criteria_description,
                custom_keywords, top_n, headless,
                scroll_wait_ms, per_post_timeout, enrich_total_timeout,
                model,
            ],
            outputs=[log_output, results_table, export_btn],
        )

        stop_btn.click(fn=stop_scraper_fn, outputs=log_output)
        stop_btn_res.click(fn=stop_scraper_fn, outputs=log_output)

    return demo


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
