"""
analyzer.py — Polish NLP pipeline for data cleaning and Gemini summarization.

Pipeline:
  1. Clean text (remove HTML, emojis, newlines).
  2. Deduplicate.
  3. Send formatted JSON to Gemini to generate a Markdown summary/ranking.
"""

import json
import re
from typing import Callable
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Text Cleaning
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Remove HTML, newlines, extra whitespace, and emojis."""
    if not text:
        return ""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove URLs (optional, but usually good for "pure text")
    # text = re.sub(r'http\S+', '', text) 
    # Replace newlines with space
    text = re.sub(r'[\r\n]+', ' ', text)
    # Normalize duplicate whitespace
    text = re.sub(r'\s+', ' ', text)
    
    # Remove emojis (basic range check)
    # This regex covers many common emoji ranges but not all. 
    # For a robust solution, 'emoji' library is better, but avoiding new deps if possible.
    # Using a simple block range for now.
    try:
        # High surrogate pass for some emojis
        text = text.encode('ascii', 'ignore').decode('ascii')
    except:
        pass
    
    return text.strip()

# ---------------------------------------------------------------------------
# Gemini summarization
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """Jesteś ekspertem i analitykiem danych z mediów społecznościowych.
Twoim zadaniem jest przeanalizowanie dostarczonych postów i komentarzy (w formacie JSON) i przygotowanie raportu.

INSTRUKCJE UŻYTKOWNIKA:
{user_instructions}

FORMAT ODPOWIEDZI:
Wygeneruj czytelny raport w formacie MARKDOWN.
Raport powinien zawierać:
1. Podsumowanie ogólne (synteza najważniejszych wątków).
2. Ranking / Lista punktowa (zgodnie z instrukcjami użytkownika).
3. Wnioski.

Nie używaj tagów XML, nie zwracaj JSON. Zwróć czysty tekst Markdown.
Piszesz po polsku.
"""

def _build_summary_prompt(posts: list[dict], user_instructions: str) -> str:
    lines = [
        "Oto dane z grupy Facebook (posty i komentarze):",
        "```json"
    ]
    # Minimize JSON to save tokens
    clean_posts = []
    for p in posts:
        clean_posts.append({
            "text": clean_text(p.get("text", "")),
            "reactions": p.get("reactions", 0),
            "comments": p.get("comments", 0)
        })
    
    lines.append(json.dumps(clean_posts, ensure_ascii=False))
    lines.append("```")
    lines.append(f"\nInstrukcje dodatkowe: {user_instructions}")
    
    return "\n".join(lines)


def _call_gemini_summary(posts: list[dict], user_instructions: str, api_key: str, model: str, log: Callable) -> str:
    """
    Send all posts to Gemini to generate one big summary.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = _build_summary_prompt(posts, user_instructions)
    log(f"  📤 Sending {len(posts)} posts to Gemini (approx {len(prompt)//4} tokens)...")

    formatted_system_prompt = _SYSTEM_PROMPT.format(user_instructions=user_instructions)

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=formatted_system_prompt,
                temperature=0.3,
            ),
        )
        return response.text.strip()

    except Exception as e:
        log(f"⚠️ Gemini error: {e}")
        return f"❌ Wystąpił błąd podczas generowania raportu: {e}"

# ---------------------------------------------------------------------------
# Main analysis pipeline
# ---------------------------------------------------------------------------

def process_and_summarize(
    posts: list[dict],
    user_instructions: str,
    gemini_api_key: str,
    model: str,
    log: Callable,
) -> tuple[str, pd.DataFrame]:
    """
    1. Clean data.
    2. Deduplicate.
    3. Send to Gemini for Markdown summary.
    """
    if not posts:
        log("⚠️ No posts to analyze.")
        return "", pd.DataFrame()

    log(f"🧹 Cleaning and deduplicating {len(posts)} posts...")
    
    # Deduplicate
    seen_hashes = set()
    deduped = []
    import hashlib
    
    for p in posts:
        # Clean text first
        cleaned_text = clean_text(p["text"])
        if not cleaned_text:
            continue
            
        norm = re.sub(r'\s+', ' ', cleaned_text).lower()
        h = hashlib.md5(norm.encode()).hexdigest()
        
        if h not in seen_hashes:
            seen_hashes.add(h)
            # Store cleaned text back in the dict for the DF/LLM
            p["cleaned_text"] = cleaned_text
            deduped.append(p)

    log(f"  → Result files: {len(deduped)} unique posts.")

    # Convert to DataFrame for export
    df = pd.DataFrame(deduped)
    if not df.empty:
        # Reorder columns if possible
        cols = ["cleaned_text", "reactions", "comments"]
        # Add others if exist
        for c in df.columns:
            if c not in cols:
                cols.append(c)
        df = df[cols]

    if not gemini_api_key:
        log("⚠️ No Gemini API key provided. Skipping LLM summary.")
        return "⚠️ Brak klucza API. Nie wygenerowano podsumowania.", df

    log("🤖 Generating summary with Gemini...")
    summary = _call_gemini_summary(deduped, user_instructions, gemini_api_key, model, log)
    
    log("✅ Report generated.")
    return summary, df
