import json
import os
import re
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator"
OUT_PATH = "data/booksy-prices.json"

PRICE_RE = re.compile(r"(\d{1,4},\d{2})\s*zł(\+)?", re.IGNORECASE)
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min)\b", re.IGNORECASE)

KNOWN_CATEGORIES = {
    "Popularne usługi", "Usługi męskie", "Usługi damskie", "Koloryzacja",
    "Zabiegi regenerujące włosy", "Prostowanie", "Fryzura okolicznościowa", "Fryzury okolicznościowe",
    "Trwała ondulacja"
}

# Główne kategorie, które będą trzymać warianty
GROUPABLE_SERVICES = {
    "Strzyżenie damskie + mycie + stylizacja", "Koloryzacja włosów", "Farbowanie-odrost",
    "Metamorfoza koloru", "Rozjaśnianie - odrost", "Refleksy/sombre", "Dekoloryzacja włosów",
    "Baleyage", "Trwała ondulacja"
}

def clean(s): 
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()

def is_price(s): 
    return bool(PRICE_RE.search(s))

def is_dur(s): 
    return bool(DUR_RE.search(s))

def is_count(s): 
    return bool(re.fullmatch(r"\d+\s+usług[iy]?", s, re.IGNORECASE))

def looks_like_variant(name):
    # Sprawdza czy usługa jest podrzędnym wariantem (uwzględnia Metamorfozę!)
    n = name.lower()
    keywords = ["włosy", "wlosy", "metamorfoza", "krótkie", "długie", "średnie", "ramion", "uszu", "odrost"]
    return any(k in n for k in keywords)

def parse():
    # Używamy zwykłego requests, które działało u Ciebie wcześniej do pobrania HTML
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    response = requests.get(BOOKSY_URL, headers=headers, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Wyciągamy czysty tekst linijka po linijce
    lines = [clean(l) for l in soup.get_text("\n").splitlines() if clean(l)]
    
    try:
        start_idx = lines.index("Usługi")
    except ValueError:
        return {"error": "Nie znaleziono sekcji Usługi. Strona mogła się nie załadować w całości."}

    sec = lines[start_idx:]
    categories = []
    current_cat = None
    current_group = None

    i = 0
    while i < len(sec):
        line = sec[i]

        # 1. Wykrywanie nowej sekcji/kategorii
        if line in KNOWN_CATEGORIES or (i+1 < len(sec) and is_count(sec[i+1])):
            current_cat = {"name": line, "items": []}
            categories.append(current_cat)
            i += 2 if (i+1 < len(sec) and is_count(sec[i+1])) else 1
            current_group = None
            continue

        if not current_cat:
            i += 1
            continue

        # 2. Szukanie bloku konkretnej usługi (zakończonego słowem Umów)
        if line != "Umów" and not is_price(line) and not is_dur(line):
            title = line
            price, duration = None, None
            
            # Przeszukujemy do 12 linii w dół w poszukiwaniu ceny przed przyciskiem "Umów"
            for j in range(1, 13):
                if i + j >= len(sec): break
                sub_line = sec[i+j]
                
                # Zapisujemy znalezioną cenę i czas
                if is_price(sub_line): 
                    price = PRICE_RE.search(sub_line).group(0)
                if is_dur(sub_line): 
                    duration = sub_line
                
                # Uderzyliśmy w "Umów" -> Zapisujemy paczkę danych
                if sub_line == "Umów":
                    if price:
                        entry = {"name": title, "price": price, "duration": duration or ""}
                        
                        # Jeśli to nazwa-rodzic (np. Strzyżenie damskie), twórz grupę
                        if title in GROUPABLE_SERVICES:
                            current_group = {"name": title, "variants": []}
                            current_cat["items"].append(current_group)
                        
                        # Jeśli mamy grupę otwartą i tytuł to np. Metamorfoza, wrzucaj do wariantów
                        elif current_group and looks_like_variant(title):
                            current_group["variants"].append(entry)
                        
                        # Zwykła pojedyncza usługa
                        else:
                            current_cat["items"].append(entry)
                            current_group = None
                            
                    i += j # Przewijamy indeks o te przeczytane linie
                    break
                
                # Jeśli po drodze trafimy na kategorię, przerywamy pętlę, to był fałszywy alarm
                if sub_line in KNOWN_CATEGORIES: 
                    break
        
        i += 1

    return {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": [c for c in categories if c["items"]]
    }

if __name__ == "__main__":
    data = parse()
    if "error" in data:
        print(f"BŁĄD: {data['error']}")
        exit(1)
        
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Pomyślnie zaktualizowano JSON. Zapisano w {OUT_PATH}")
