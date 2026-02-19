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
    load_runs,
    save_run,
    clear_session_metadata,
)


def create_demo(run_pipeline_fn, clear_session_fn, session_status_fn, stop_scraper_fn):
    """
    Create and return the Gradio Blocks demo.
    Dependencies are injected to avoid circular imports.
    """
    _cfg = load_settings()

    with gr.Blocks(title="📊 Facebook Group Scraper") as demo:

        gr.HTML("""
        <div class="app-header">
            <h1>📊 Facebook Group Scraper</h1>
            <p>Znajdź najczęstsze pytania i problemy w grupach na Facebooku • Analiza po polsku</p>
        </div>
        """)
# ... (rest of the layout code matches existing lines until end of file)
# We need to target the end of the file for CUSTOM_JS, and the beginning for gr.Blocks.
# Since replace_file_content is single block, I will do two calls if needed, or just one if I can match enough context?
# Use multi_replace for safety.

        with gr.Tabs() as tabs:

            # ── Tab 1: Instrukcja + Log + Wynik ──────────────────────────────
            with gr.Tab("🚀 Start & Wyniki", id="main"):
                
                gr.HTML('<div class="section-title">1. Link do grupy</div>')
                with gr.Row():
                    group_url = gr.Textbox(
                        label="URL grupy",
                        placeholder="https://www.facebook.com/groups/nazwa-grupy",
                        value=_cfg["group_url"],
                        scale=3,
                    )
                    history_dropdown = gr.Dropdown(
                        label="📂 Ostatnie grupy",
                        choices=history_choices(),
                        value=None,
                        interactive=True,
                        scale=1
                    )

                with gr.Row():
                    with gr.Column(scale=3):
                        gr.HTML('<div class="section-title">2. Instrukcje dla AI</div>')
                        criteria_description = gr.Textbox(
                            label="Co ma zawierać raport?",
                            value=_cfg["criteria_description"],
                            lines=3,
                            placeholder="np. Stwórz ranking top 10 problemów z dietą...",
                            elem_classes="input-instruction"
                        )
                        criteria_preset = gr.Dropdown(
                            label="📂 Wczytaj szablon",
                            choices=load_presets("criteria"),
                            value=None,
                            interactive=True,
                            elem_classes="preset-dropdown"
                        )
                    
                    with gr.Column(scale=2):
                        gr.HTML('<div class="section-title">3. Sterowanie</div>')
                        start_btn = gr.Button("🚀 Rozpocznij Scrapowanie", variant="primary", size="lg")
                        stop_btn = gr.Button("🛑 Zatrzymaj", variant="stop")
                
                gr.HTML('<div class="section-title" style="margin-top: 20px;">4. Log Postępu</div>')
                log_output = gr.Textbox(
                    label="Logi",
                    lines=8,
                    interactive=False,
                    elem_classes="log-area",
                    autoscroll=True
                )

                gr.HTML('<div class="section-title" style="margin-top: 20px;">5. Wyniki (Markdown)</div>')
                results_md = gr.Markdown(
                    label="Raport",
                    elem_classes="results-markdown",
                    min_height=400
                )
                
                with gr.Row():
                    # Custom Copy Button
                    copy_btn = gr.Button("📋 Kopiuj do schowka", variant="secondary", elem_id="copy-btn")
                    export_btn = gr.DownloadButton(
                        label="📥 Pobierz dane CSV",
                        variant="secondary",
                        visible=False,
                    )

            # ── Tab 2: Historia Wyników ──────────────────────────────────────
            with gr.Tab("📂 Historia wyników", id="history"):
                gr.Markdown("### Ostatnie analizy")
                
                run_history = gr.Dataframe(
                    headers=["Data", "Grupa", "Podsumowanie"],
                    datatype=["str", "str", "str"],
                    interactive=False,
                    wrap=True,
                )
                
                refresh_history_btn = gr.Button("🔄 Odśwież historię")

                def get_history_df():
                    runs = load_runs()
                    data = []
                    for r in runs:
                        snippet = r.get("summary", "")[:100] + "..." if len(r.get("summary", "")) > 100 else r.get("summary", "")
                        data.append([r.get("date", ""), r.get("group_name", ""), snippet])
                    return data

                gr.HTML('<div class="section-title" style="margin-top: 20px;">📜 Szczegóły wybranego raportu</div>')
                history_details = gr.Markdown(
                    value="Pobierz historię i kliknij w wiersz tabeli, aby zobaczyć szczegóły.",
                    elem_classes="results-markdown",
                    min_height=400
                )

                def show_details(evt: gr.SelectData):
                    runs = load_runs()
                    if 0 <= evt.index[0] < len(runs):
                        return runs[evt.index[0]].get("summary", "")
                    return ""

                refresh_history_btn.click(fn=get_history_df, outputs=run_history)
                run_history.select(fn=show_details, outputs=history_details)
                
                # Load on init
                demo.load(fn=get_history_df, outputs=run_history)

            # ── Tab 3: Konfiguracja ──────────────────────────────────────────
            with gr.Tab("⚙️ Konfiguracja", id="config"):
                
                gr.HTML('<div class="section-title">🔐 Dane logowania</div>')
                with gr.Row():
                    email = gr.Textbox(label="E-mail", value=_cfg["email"], scale=2)
                    password = gr.Textbox(label="Hasło", type="password", scale=2)
                    save_session = gr.Checkbox(label="💾 Zapisz sesję", value=_cfg["save_session"], scale=1)
                    
                    # Session management
                    session_status_md = gr.Markdown(value=session_status_fn(_cfg["email"]))
                    clear_session_btn = gr.Button("🗑️ Usuń sesję", size="sm", variant="secondary")

                gr.HTML('<div class="section-title">🤖 Gemini API & Model</div>')
                with gr.Row():
                    gemini_api_key = gr.Textbox(
                        label="Klucz API",
                        type="password",
                        value=_cfg["gemini_api_key"],
                        scale=2
                    )
                    model = gr.Dropdown(
                        label="Model",
                        choices=["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
                        value=_cfg["model"],
                        scale=1
                    )
                    headless = gr.Checkbox(label="Headless (bez okna)", value=_cfg["headless"])

                gr.HTML('<div class="section-title">📈 Parametry Scrapowania</div>')
                with gr.Row():
                    max_posts = gr.Slider(label="Max postów", minimum=20, maximum=500, value=_cfg["max_posts"], step=10)
                    scroll_wait_ms = gr.Slider(label="Scroll wait (ms)", minimum=500, maximum=5000, value=_cfg["scroll_wait_ms"], step=250)
                
                with gr.Row():
                    per_post_timeout = gr.Slider(label="Timeout post (s)", minimum=1, maximum=30, value=_cfg["per_post_timeout"])
                    enrich_total_timeout = gr.Slider(label="Timeout enrichment (s)", minimum=10, maximum=300, value=_cfg["enrich_total_timeout"])

        # ── Events ───────────────────────────────────────────────────────────────

        # Config auto-save
        def _save(key):
            return lambda v: save_settings(**{key: v})

        group_url.change(fn=_save("group_url"), inputs=group_url)
        email.change(fn=_save("email"), inputs=email)
        save_session.change(fn=_save("save_session"), inputs=save_session)
        max_posts.change(fn=_save("max_posts"), inputs=max_posts)
        criteria_description.change(fn=_save("criteria_description"), inputs=criteria_description)
        gemini_api_key.change(fn=_save("gemini_api_key"), inputs=gemini_api_key)
        headless.change(fn=_save("headless"), inputs=headless)
        model.change(fn=_save("model"), inputs=model)

        # Helpers
        history_dropdown.change(fn=url_from_choice, inputs=history_dropdown, outputs=group_url)
        criteria_preset.change(fn=lambda v: v, inputs=criteria_preset, outputs=criteria_description)
        
        clear_session_btn.click(fn=clear_session_fn, inputs=email, outputs=session_status_md)
        email.change(fn=session_status_fn, inputs=email, outputs=session_status_md)

        # Main Pipeline
        # Note: top_n and custom_keywords removed from UI per request (focus on prompt)
        # We pass default/dummy values for valid pipeline signature if needed, or update signature.
        # Let's assume pipeline is updated or we pass from persistence defaults.
        
        start_btn.click(
            fn=run_pipeline_fn,
            inputs=[
                group_url, email, password, max_posts, save_session,
                gemini_api_key, criteria_description,
                gr.State(""), # custom_keywords (removed)
                gr.State(20), # top_n (removed)
                headless,
                scroll_wait_ms, per_post_timeout, enrich_total_timeout,
                model,
            ],
            outputs=[log_output, results_md, export_btn],
        ).then(
             fn=get_history_df, outputs=run_history # Refresh history after run
        )

        stop_btn.click(fn=stop_scraper_fn, outputs=log_output)
    
    return demo


CUSTOM_CSS = """
/* ── Layout ── */
.app-header { 
    text-align: center; 
    margin-bottom: 20px; 
    padding: 20px;
    background: linear-gradient(135deg, #1877f2 0%, #0d5dbf 100%);
    color: white;
    border-radius: 12px;
}
.app-header h1 { margin: 0; font-size: 1.8rem; }
.section-title {
    font-size: 0.85rem; font-weight: bold; text-transform: uppercase; color: #65676b; margin-bottom: 8px;
}
.log-area textarea {
    font-family: monospace; font-size: 12px; background: #1a1a2e; color: #ddd;
}
.results-markdown {
    padding: 20px; background: white; border: 1px solid #ddd; border-radius: 8px;
}
"""

CUSTOM_JS = """
function() {
    // Copy to clipboard functionality
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('#copy-btn');
        if (btn) {
            const md = document.querySelector('.results-markdown');
            if (md) {
                // Get text, but also try to get the raw markdown if possible? 
                // gr.Markdown renders HTML. getting innerText gives visible text.
                // The user probably wants the generated markdown report.
                // But the Markdown component renders it. 
                // innerText is usually what people want (the report content).
                const text = md.innerText;
                
                if (!navigator.clipboard) {
                    btn.innerText = '❌ Brak dostępu do schowka';
                    return;
                }

                navigator.clipboard.writeText(text).then(() => {
                    const originalText = btn.innerText;
                    btn.innerText = '✅ Skopiowano!';
                    setTimeout(() => btn.innerText = '📋 Kopiuj do schowka', 2000);
                }).catch(err => {
                    console.error('Clipboard copy failed:', err);
                    btn.innerText = '❌ Błąd';
                });
            }
        }
    });
}
"""
