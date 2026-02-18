"""
app.py — Entry point for the Facebook Group Scraper.

Run with:
    python app.py
Then open http://localhost:7860 in your browser.
"""

import gradio as gr
from dotenv import load_dotenv

from app.core import pipeline
from app.ui import layout

load_dotenv()


if __name__ == "__main__":
    demo = layout.create_demo(
        run_pipeline_fn=pipeline.run_pipeline,
        clear_session_fn=pipeline.clear_session,
        session_status_fn=pipeline.session_status,
        stop_scraper_fn=pipeline.stop_scraper,
    )
    
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        inbrowser=True,
        css=layout.CUSTOM_CSS,
        theme=gr.themes.Soft(
            primary_hue="blue",
            secondary_hue="slate",
            neutral_hue="slate",
            font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui"],
        ),
    )
