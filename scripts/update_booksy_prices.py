import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

PRICE_RE = re.compile(r"(\d{1,4},\d{2})\s*zł(\+)?", re.IGNORECASE)
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min)\b", re.IGNORECASE)

KNOWN_CATEGORIES = {
    "Popularne usługi",
    "Usługi męskie",
    "Usługi damskie",
    "Koloryzacja",
    "Zabiegi regenerujące włosy",
    "Prostowanie",
    "Fryzury okolicznościowe",
    "Trwała ondulacja",
}

GROUPABLE_SERVICES = {
    "Strzyżenie damskie + mycie + stylizacja",
    "Koloryzacja włosów",
    "Farbowanie-odrost",
    "Metamorfoza koloru",
    "Rozjaśnianie - odrost",
    "Refleksy/sombre",
    "Dekoloryzacja włosów",
    "Baleyage",
    "Trwała ondulacja",
}

def clean(s: str) -> str:
    s = s.replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()

def is_price(s: str): return bool(PRICE_RE.search(s))
def is_dur(s: str): return bool(DUR_RE.search(s))
def is_count(s: str): return bool(re.fullmatch(r"\d+\s+usług[iy]?", s))

def looks_like_variant(name: str) -> bool:
    n = name.lower()
    keywords = ["włosy", "wlosy", "metamorfoza", "odrost", "krótkie", "długie", "średnie", "ramion"]
    return any(k in n for k in keywords)

def parse():
    response = requests.get(BOOKSY_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Wyciągamy czysty tekst, zachowując strukturę linii
    lines = [clean(l) for l in soup.get_text("\n").splitlines() if clean(l)]
    
    # Szukamy sekcji Usługi
    try:
        start_idx = lines.index("Usługi")
    except ValueError:
        return {"error": "Nie znaleziono sekcji Usługi"}

    sec = lines[start_idx:]
    categories = []
    current_cat = None
    current_group = None

    i = 0
    while i < len(sec):
        line = sec[i]

        # Wykrywanie kategorii
        if line in KNOWN_CATEGORIES or (i+1 < len(sec) and is_count(sec[i+1])):
            current_cat = {"name": line, "items": []}
            categories.append(current_cat)
            i += 2 if (i+1 < len(sec) and is_count(sec[i+1])) else 1
            current_group = None
            continue

        if not current_cat:
            i += 1
            continue

        # Szukamy bloku usługi (Nazwa -> ... -> Cena -> Czas -> Umów)
        if line != "Umów" and not is_price(line) and not is_dur(line):
            title = line
            price, duration = None, None
            
            # Przeszukujemy max 10 linii w dół w poszukiwaniu ceny i czasu przed przyciskiem Umów
            for j in range(1, 11):
                if i + j >= len(sec): break
                sub_line = sec[i+j]
                
                if is_price(sub_line): price = PRICE_RE.search(sub_line).group(0)
                if is_dur(sub_line): duration = sub_line
                
                if sub_line == "Umów":
                    if price:
                        entry = {"name": title, "price": price, "duration": duration or ""}
                        
                        # Sprawdzamy czy to główna usługa grupująca
                        if title in GROUPABLE_SERVICES:
                            current_group = {"name": title, "variants": []}
                            current_cat["items"].append(current_group)
                        # Sprawdzamy czy to wariant należący do grupy
                        elif current_group and looks_like_variant(title):
                            current_group["variants"].append(entry)
                        # Zwykła usługa
                        else:
                            current_cat["items"].append(entry)
                            current_group = None
                            
                    i += j # Przeskakujemy przetworzony blok
                    break
                
                # Jeśli trafimy na nową kategorię przed "Umów", to nie jest to usługa
                if sub_line in KNOWN_CATEGORIES: break
        
        i += 1

    return {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": [c for c in categories if c["items"]]
    }

if __name__ == "__main__":
    data = parse()
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
