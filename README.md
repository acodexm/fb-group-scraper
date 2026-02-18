# 📊 Facebook Group Scraper

Narzędzie do scrapowania grup na Facebooku w poszukiwaniu najczęstszych pytań i problemów użytkowników. Posiada prosty interfejs webowy (Gradio) — nie wymaga znajomości programowania.

---

## 🚀 Instalacja (jednorazowa)

### 1. Wymagania

- **Python 3.10+** — pobierz z [python.org](https://www.python.org/downloads/)
- Dostęp do terminala (macOS: aplikacja „Terminal")

### 2. Zainstaluj zależności

Otwórz terminal, przejdź do folderu projektu i wykonaj:

```bash
cd /Users/acodexm/code/facebook-scraper
pip install -r requirements.txt
playwright install chromium
```

> Instalacja zajmuje kilka minut (pobieranie przeglądarki Chromium ~150 MB).

### 3. (Opcjonalnie) Klucz API Gemini

Jeśli chcesz korzystać z inteligentnego grupowania pytań przez AI:

1. Wejdź na [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) i wygeneruj bezpłatny klucz
2. Skopiuj plik `.env.example` jako `.env`:
   ```bash
   cp .env.example .env
   ```
3. Otwórz plik `.env` w edytorze i wklej swój klucz:
   ```
   GEMINI_API_KEY=AIza...twój_klucz...
   ```

---

## ▶️ Uruchomienie

```bash
python app.py
```

Przeglądarka otworzy się automatycznie pod adresem **http://localhost:7860**

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
├── requirements.txt
├── .env.example    # Szablon pliku z kluczem API
└── README.md
```
