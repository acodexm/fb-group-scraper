"""
analyzer.py — Polish NLP pipeline for question detection and Gemini analysis.

Pipeline:
  1. Regex pre-filter: keep posts containing ? or interrogative words (PL + EN)
  2. Rank by engagement: score = reactions + (comments × 3)
  3. Gemini batch analysis: returns original_question, summary (PL), category (PL)
  4. Final sort by score, return top N
"""

import json
import re
from typing import Callable

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Regex pre-filter patterns (PL + EN interrogatives)
# ---------------------------------------------------------------------------

# Polish interrogative words
_PL_INTERROGATIVES = [
    r"\bczy\b", r"\bjak\b", r"\bco\b", r"\bgdzie\b", r"\bkiedy\b",
    r"\bdlaczego\b", r"\bczemu\b", r"\bkto\b", r"\bkogo\b", r"\bkomu\b",
    r"\bile\b", r"\bktóry\b", r"\bktóra\b", r"\bktóre\b", r"\bpo co\b",
    r"\bw jaki sposób\b", r"\bskąd\b", r"\bna co\b", r"\bz czym\b",
    r"\bjakie\b", r"\bjaki\b", r"\bjaka\b", r"\bczym\b",
]

# English interrogative words
_EN_INTERROGATIVES = [
    r"\bhow\b", r"\bwhat\b", r"\bwhere\b", r"\bwhen\b", r"\bwhy\b",
    r"\bwho\b", r"\bwhich\b", r"\bis there\b", r"\bare there\b",
    r"\bdoes\b", r"\bdo\b", r"\bcan\b", r"\bcould\b", r"\bshould\b",
    r"\banyone\b", r"\bhas anyone\b", r"\bhave you\b",
]

_ALL_INTERROGATIVES = _PL_INTERROGATIVES + _EN_INTERROGATIVES


def _is_question(text: str) -> bool:
    """Return True if the post looks like a question (syntactic check only)."""
    if "?" in text:
        return True
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in _ALL_INTERROGATIVES)


# ---------------------------------------------------------------------------
# Gemini batch analysis
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """Jesteś analitykiem grup społecznościowych na Facebooku.
Otrzymasz listę postów z grupy. Twoje zadanie:
1. Przeanalizuj każdy post pod kątem podanych kryteriów wyszukiwania.
2. Dla każdego posta który pasuje do kryteriów, zwróć obiekt JSON.
3. Dla postów które NIE pasują do kryteriów, zwróć null.

Zasady:
- Odpowiadaj ZAWSZE po polsku (nawet jeśli post jest po angielsku).
- "summary" to jedno zdanie (max 150 znaków) opisujące problem/pytanie użytkownika.
- "category" to krótka etykieta (2-4 słowa) np. "Dieta i odżywianie", "Motywacja", "Sprzęt sportowy".
- "original_question" to oryginalne pytanie/post (przepisz dosłownie, bez skracania).
- Zwróć TYLKO tablicę JSON, bez żadnego dodatkowego tekstu ani markdown.
"""


def _build_user_prompt(posts_with_scores: list[dict], criteria: str) -> str:
    lines = [
        f"Kryteria wyszukiwania: {criteria}",
        "",
        "Posty do analizy (format: [indeks] [wynik_zaangażowania] tekst):",
        "",
    ]
    for i, p in enumerate(posts_with_scores):
        score = p["reactions"] + p["comments"] * 3
        # Truncate very long posts to ~800 chars to save tokens
        text = p["text"]
        if len(text) > 800:
            text = text[:797] + "..."
        lines.append(f"[{i}] [score:{score}] {text}")
        lines.append("")

    lines.append(
        f"Zwróć tablicę JSON z {len(posts_with_scores)} elementami "
        f"(null dla postów nie pasujących do kryteriów). Przykład:\n"
        f'[{{"original_question": "...", "summary": "...", "category": "..."}}, null, ...]'
    )
    return "\n".join(lines)


def _call_gemini_batch(posts: list[dict], criteria: str, api_key: str, log: Callable) -> list[dict | None]:
    """
    Send a batch of posts to Gemini. Returns list of result dicts or None per post.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    prompt = _build_user_prompt(posts, criteria)

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        raw = response.text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            log("⚠️ Gemini returned unexpected format, skipping batch.")
            return [None] * len(posts)

        # Pad/trim to match input length
        while len(parsed) < len(posts):
            parsed.append(None)
        return parsed[:len(posts)]

    except Exception as e:
        log(f"⚠️ Gemini error: {e}")
        return [None] * len(posts)


# ---------------------------------------------------------------------------
# Main analysis pipeline
# ---------------------------------------------------------------------------

def analyze_posts(
    posts: list[dict],
    custom_keywords: list[str],
    top_n: int,
    gemini_api_key: str,
    criteria_description: str,
    log: Callable,
) -> pd.DataFrame:
    """
    Main analysis pipeline.

    Returns a DataFrame with columns:
      rank, original_question, summary, category, reactions, comments, score
    """
    if not posts:
        log("⚠️ No posts to analyze.")
        return pd.DataFrame()

    # Step 1: Regex pre-filter — keep questions (syntactic) + custom keywords
    log(f"🔍 Pre-filtering {len(posts)} posts for questions...")
    filtered = []
    for p in posts:
        text = p["text"]
        is_q = _is_question(text)
        has_custom = any(kw.lower() in text.lower() for kw in custom_keywords if kw.strip())
        if is_q or has_custom:
            filtered.append(p)

    log(f"  → {len(filtered)} posts contain questions or match keywords.")

    if not filtered:
        log("⚠️ No questions found after pre-filtering. Try broader criteria or more posts.")
        return pd.DataFrame()

    # Step 2: Compute engagement score and sort
    for p in filtered:
        p["score"] = p.get("reactions", 0) + p.get("comments", 0) * 3

    filtered.sort(key=lambda p: p["score"], reverse=True)

    # Cap at top_n * 3 before sending to Gemini (avoid huge API calls)
    candidate_pool = filtered[: top_n * 3]
    log(f"  → Sending top {len(candidate_pool)} posts to Gemini for analysis...")

    if not gemini_api_key:
        log("⚠️ No Gemini API key provided. Returning raw pre-filtered questions without AI analysis.")
        rows = []
        for i, p in enumerate(candidate_pool[:top_n], 1):
            rows.append({
                "rank": i,
                "original_question": p["text"][:300],
                "summary": "(brak klucza API Gemini — brak podsumowania)",
                "category": "—",
                "reactions": p.get("reactions", 0),
                "comments": p.get("comments", 0),
                "score": p["score"],
            })
        return pd.DataFrame(rows)

    # Step 3: Gemini batch analysis (in batches of 20)
    BATCH_SIZE = 20
    all_results: list[dict | None] = []

    for batch_start in range(0, len(candidate_pool), BATCH_SIZE):
        batch = candidate_pool[batch_start: batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(candidate_pool) + BATCH_SIZE - 1) // BATCH_SIZE
        log(f"  🤖 Gemini batch {batch_num}/{total_batches} ({len(batch)} posts)...")

        results = _call_gemini_batch(batch, criteria_description, gemini_api_key, log)
        all_results.extend(results)

    # Step 4: Merge results with posts, filter nulls, sort by score
    rows = []
    for post, result in zip(candidate_pool, all_results):
        if result is None:
            continue
        if not isinstance(result, dict):
            continue
        original = result.get("original_question") or post["text"][:300]
        summary = result.get("summary", "")
        category = result.get("category", "—")
        if not summary:
            continue
        rows.append({
            "original_question": original,
            "summary": summary,
            "category": category,
            "reactions": post.get("reactions", 0),
            "comments": post.get("comments", 0),
            "score": post["score"],
        })

    if not rows:
        log("⚠️ Gemini found no posts matching the criteria.")
        return pd.DataFrame()

    # Sort by score descending, take top N
    rows.sort(key=lambda r: r["score"], reverse=True)
    rows = rows[:top_n]

    for i, row in enumerate(rows, 1):
        row["rank"] = i

    df = pd.DataFrame(
        rows,
        columns=["rank", "original_question", "summary", "category", "reactions", "comments", "score"],
    )
    log(f"✅ Analysis complete. {len(df)} results ready.")
    return df
