from pathlib import Path
from app.persistence import get_session_file_path

def test_session_paths():
    assert get_session_file_path("") == Path(".fb_session.json")
    assert get_session_file_path("test@example.com") == Path(".fb_session_test_example_com.json")
    assert get_session_file_path("user+alias@gmail.com") == Path(".fb_session_user_alias_gmail_com.json")
    print("✅ Session path logic verified.")

if __name__ == "__main__":
    test_session_paths()
