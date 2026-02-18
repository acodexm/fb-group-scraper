import sys
sys.path.append(".")  # Allow importing modules from root

import pytest
from analyzer import _is_question, filter_questions, deduplicate_posts


def test_is_question_pl():
    assert _is_question("Czy to jest pytanie?") == True
    assert _is_question("Jak to zrobić?") == True
    assert _is_question("Co o tym sądzicie") == True  # Interrogative word but no question mark
    assert _is_question("Gdzie to kupić") == True


def test_is_question_en():
    assert _is_question("How do I do this?") == True
    assert _is_question("What is the best...") == True
    assert _is_question("Does anyone know") == True
    assert _is_question("Is there a way") == True


def test_is_not_question():
    assert _is_question("To jest zwykłe zdanie.") == False
    assert _is_question("Sprzedam opony.") == False
    assert _is_question("Dzień dobry wszystkim.") == False
    assert _is_question("Polecam ten produkt.") == False


def test_filter_questions():
    posts = [
        {"text": "Czy ktoś wie jak to naprawić?", "id": 1},
        {"text": "Sprzedam opla.", "id": 2},
        {"text": "Szukam pomocy z dieta.", "id": 3},
        {"text": "To jest spam.", "id": 4}
    ]
    keywords = ["dieta"]
    
    filtered = filter_questions(posts, keywords)
    texts = [p["text"] for p in filtered]
    
    assert "Czy ktoś wie jak to naprawić?" in texts
    assert "Szukam pomocy z dieta." in texts
    assert "Sprzedam opla." not in texts
    assert "To jest spam." not in texts


def test_deduplicate_posts():
    posts = [
        {"text": "To jest unikalny post.", "id": 1},
        {"text": "To jest duplikat.", "id": 2},
        {"text": "to  jest  duplikat.", "id": 3},  # Should normalize to same hash
        {"text": "To jest inny post.", "id": 4},
    ]
    
    deduped = deduplicate_posts(posts)
    texts = [p["text"] for p in deduped]
    
    assert len(deduped) == 3
    assert "To jest unikalny post." in texts
    assert "To jest inny post." in texts
    # One of the duplicates should remain
    assert ("To jest duplikat." in texts) or ("to  jest  duplikat." in texts)


def test_deduplicate_long_diff():
    # Ensure long posts that start same but differ at end are NOT deduplicated
    p1 = "A" * 300 + "B"
    p2 = "A" * 300 + "C"
    
    posts = [{"text": p1}, {"text": p2}]
    deduped = deduplicate_posts(posts)
    
    assert len(deduped) == 2
