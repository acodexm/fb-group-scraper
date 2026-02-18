import sys
sys.path.append(".")  # Allow importing modules from root

import pytest
import json
from pathlib import Path
from unittest.mock import patch

# Import correct modules from new package structure
from app import persistence
from app.core import pipeline


def test_parse_custom_keywords():
    # Function moved to pipeline module and renamed (no leading underscore)
    result = pipeline.parse_custom_keywords("foo, bar, baz ")
    assert result == ["foo", "bar", "baz"]
    
    result = pipeline.parse_custom_keywords("  test  ")
    assert result == ["test"]
    
    result = pipeline.parse_custom_keywords("")
    assert result == []


def test_url_from_choice():
    # Function moved to persistence module and renamed (no leading underscore)
    # Format is usually "Name — URL"
    choice = "My Group — https://facebook.com/groups/123"
    url = persistence.url_from_choice(choice)
    assert url == "https://facebook.com/groups/123"
    
    # Handle direct URL input if possible (though the function assumes choice format)
    choice = "Just a string"
    url = persistence.url_from_choice(choice)
    assert url == "Just a string"


@patch("app.persistence.SETTINGS_FILE", new=Path("test_settings.json"))
def test_save_and_load_settings(tmp_path):
    # Use a temp path for the test settings file
    test_file = tmp_path / "test_settings.json"
    
    with patch("app.persistence.SETTINGS_FILE", test_file):
        # Initial load should return defaults
        defaults = persistence.load_settings()
        assert defaults["group_url"] == ""
        assert defaults["max_posts"] == 100
        
        # Save a setting
        persistence.save_settings(group_url="https://test.com", max_posts=50)
        
        # Verify file content
        content = json.loads(test_file.read_text(encoding="utf-8"))
        assert content["group_url"] == "https://test.com"
        assert content["max_posts"] == 50
        
        # Load again
        loaded = persistence.load_settings()
        assert loaded["group_url"] == "https://test.com"
        assert loaded["max_posts"] == 50
        # Check defaults are preserved for missing keys
        assert loaded["headless"] == True  # from default


@patch("app.persistence.GROUPS_HISTORY_FILE", new=Path("test_history.json"))
def test_history_save(tmp_path):
    test_file = tmp_path / "test_history.json"
    
    with patch("app.persistence.GROUPS_HISTORY_FILE", test_file):
        # Save a URL
        url = "https://facebook.com/groups/my-group"
        persistence.save_to_history(url)
        
        # Verify
        history = persistence.load_history()
        assert len(history) == 1
        assert history[0]["url"] == url
        assert history[0]["name"] == "My Group"
        
        # Save duplicate - expecting it to move to top or replace
        persistence.save_to_history(url)
        history = persistence.load_history()
        assert len(history) == 1
        
        # Save another
        url2 = "https://facebook.com/groups/other"
        persistence.save_to_history(url2)
        history = persistence.load_history()
        assert len(history) == 2
        assert history[0]["url"] == url2  # Newest first
