# 📊 Facebook Group Scraper

Narzędzie do scrapowania grup na Facebooku w poszukiwaniu najczęstszych pytań i problemów użytkowników. Posiada prosty interfejs webowy (Gradio) — nie wymaga znajomości programowania.

---

## 🍎 Instrukcja krok po kroku (macOS)

Jeśli zaczynasz od zera, wykonaj poniższe kroki w aplikacji **Terminal**.

### 1. Przygotuj środowisko

1. Zainstaluj **Python 3.10+** (jeśli nie masz):
   - Pobierz z [python.org/downloads/macos](https://www.python.org/downloads/macos/) i zainstaluj.
   - W trakcie instalacji zaznacz opcję "Install certificates" (częste na macOS).
2. Otwórz Terminal i sprawdź wersję:
   ```bash
   python3 --version
   ```

### 2. Pobierz i zainstaluj projekt

Wpisz (lub skopiuj) poniższe komendy w Terminalu:

```bash
# 1. Przejdź do folderu, gdzie chcesz trzymać projekt (np. Dokumenty)
cd ~/Documents

# 2. Pobierz projekt (jeśli masz ZIP, pomiń ten krok i wejdź do rozpakowanego folderu)
git clone https://your-repo-url/facebook-scraper.git
cd facebook-scraper

# 3. Utwórz "wirtualne środowisko" (izolowany system dla tego projektu)
python3 -m venv venv

# 4. Aktywuj to środowisko (tę komendę trzeba wpisać ZAWSZE przed pracą)
source venv/bin/activate

# 5. Zainstaluj wymagane biblioteki
pip install --upgrade pip
pip install -r requirements.txt

# 6. Zainstaluj silnik przeglądarki
playwright install chromium

# 7. (Opcjonalnie) Nadaj uprawnienia do uruchamiania skryptu jednym kliknięciem
chmod +x start_app.command
```

### 3. Konfiguracja Klucza AI (Opcjonalne)

Aby raporty były inteligentnie podsumowywane przez Gemini:

1. Zdobądź darmowy klucz na [aistudio.google.com](https://aistudio.google.com/app/apikey).
2. Utwórz plik konfiguracyjny:
   ```bash
   cp .env.example .env
   open -e .env
   ```
3. W otwartym pliku wklej swój klucz po znaku równości (`GEMINI_API_KEY=...`) i zapisz (Cmd+S).

---

## ▶️ Jak uruchamiać (na co dzień)

Masz teraz dwie opcje:

### Opcja A: Kliknij i uruchom (Zalecane)
1. Wejdź do folderu `facebook-scraper` w Finderze.
2. Kliknij dwukrotnie plik **`start_app.command`**.
   - *Za pierwszym razem:* Jeśli zobaczysz komunikat, że "nie można otworzyć aplikacji, bo pochodzi od niezidentyfikowanego dewelopera", kliknij w plik **Prawym Przyciskiem Mysz** -> wybierz **Otwórz** -> i potwierdź przyciskiem **Otwórz**.
3. Terminal otworzy się, a aplikacja powinna wystartować automatycznie w przeglądarce.

---

### Opcja B: Przez Terminal (Dla zaawansowanych)

Za każdym razem, gdy chcesz użyć programu ręcznie:

1. Otwórz Terminal.
2. Wpisz komendy:
   ```bash
   cd ~/Documents/facebook-scraper  # (lub twoja ścieżka do folderu)
   source venv/bin/activate
   python app.py
   ```
3. Otwórz w przeglądarce link, który się pojawi: **http://localhost:7860**

---

## 📖 Jak używać

### Zakładka ⚙️ Konfiguracja

| Pole | Opis |
|------|------|
| **URL grupy** | Pełny link do grupy, np. `https://www.facebook.com/groups/nazwa` |
| **E-mail / Hasło** | Twoje dane logowania do Facebooka (nie są nigdzie zapisywane) |
| **Zapisz sesję** | Zaznacz, aby nie logować się ponownie przy kolejnym uruchomieniu |
| **Maks. postów** | Ile postów pobrać (więcej = wolniej, ale dokładniej) |
| **Liczba wyników** | Ile unikalnych tematów wyświetlić (domyślnie 20) |
| **Kryteria** | Opis czego szukasz — używany przez Gemini do podsumowań |
| **Słowa kluczowe** | Dodatkowe słowa oddzielone przecinkami (np. `dieta, trening`) |
| **Gemini AI** | Włącz dla lepszego grupowania (wymaga klucza API) |
| **Tryb bez okna** | Ukrywa okno przeglądarki (wyłącz jeśli masz 2FA) |

### Zakładka 📊 Wyniki

- **Log postępu** — pokazuje co dzieje się w czasie rzeczywistym
- **Tabela wyników** — posortowane według częstotliwości + reakcji
- **Pobierz CSV** — eksportuje pełne wyniki do pliku

---

## ⚠️ Ważne informacje

- **2FA**: Jeśli masz włączone dwuetapowe logowanie, zostaw opcję „Tryb bez okna" **wyłączoną** — zobaczysz okno przeglądarki i będziesz mógł/mogła ręcznie potwierdzić logowanie.
- **Sesja**: Po zaznaczeniu „Zapisz sesję" plik `.fb_session.json` zostanie zapisany lokalnie. Możesz go usunąć przyciskiem „Usuń sesję" w UI.
- **Prywatność**: Hasło jest używane tylko podczas sesji i **nigdy nie jest zapisywane na dysku**.
- **Regulamin**: Scrapowanie Facebooka jest niezgodne z ich regulaminem. Używaj wyłącznie do celów osobistych/badawczych.

---

## 🗂️ Struktura projektu

```
facebook-scraper/
├── app.py          # Interfejs Gradio (uruchom ten plik)
├── scraper.py      # Logika scrapowania (Playwright)
├── analyzer.py     # Analiza NLP (wykrywanie pytań, grupowanie)
├── start_app.command # Skrypt uruchamiający aplikację jednym kliknięciem
├── requirements.txt
├── .env.example    # Szablon pliku z kluczem API
└── README.md
```
