import json
import os
import re
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator"
OUT_PATH = "data/booksy-prices.json"

# Regexy wyciągające poprawne formaty
PRICE_RE = re.compile(r"(\d{1,4},\d{2})\s*zł(\+)?", re.IGNORECASE)
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min)\b", re.IGNORECASE)

KNOWN_CATEGORIES = {
    "Popularne usługi", "Usługi męskie", "Usługi damskie", "Koloryzacja",
    "Zabiegi regenerujące włosy", "Prostowanie", "Fryzura okolicznościowa", "Fryzury okolicznościowe",
    "Trwała ondulacja"
}

# Usługi-rodzice, do których przypinamy warianty
GROUPABLE_SERVICES = {
    "Strzyżenie damskie + mycie + stylizacja", "Koloryzacja włosów", "Farbowanie-odrost",
    "Metamorfoza koloru", "Rozjaśnianie - odrost", "Refleksy/sombre", "Dekoloryzacja włosów",
    "Baleyage", "Trwała ondulacja", "Tonowanie włosów + strzyżenie damskie",
    "Regeneracja włosów-botox", "Silna regeneracja włosów-PRO REPAIR COMBO+SPA", "Nanoplastia",
    "Przyciemnianie koloru blond+strzyżenie"
}

def clean(s): 
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()

def is_price(s): return bool(PRICE_RE.search(s))
def extract_price(s):
    m = PRICE_RE.search(s)
    if m: return f"{m.group(1)} zł{m.group(2) or ''}"
    return s

def is_dur(s): return bool(DUR_RE.search(s))
def normalize_dur(s):
    if not s: return ""
    d = s.lower().replace(" ", "")
    return re.sub(r"(\d+)g(\d+)min", r"\1g \2min", d)

def is_count(s): return bool(re.fullmatch(r"\d+\s+usług[iy]?", s, re.IGNORECASE))

def is_noise(s):
    s_low = s.lower()
    if s in {"Zapisz termin", "Pokaż wszystkie zdjęcia", "Zarezerwuj", "Zarezerwuj wizytę"}: return True
    if s_low.startswith("portfolio usługi") or s_low.startswith("image:"): return True
    return False

def looks_like_variant(name, group_name=""):
    n = name.lower()
    gn = group_name.lower()
    
    # Rozbudowana lista słów kluczowych wariantów
    keywords = [
        "włosy", "wlosy", "metamorfoza", "krótkie", "krotkie", "długie", "dlugie", 
        "średnie", "srednie", "ramion", "uszu", "odrost", "strzyżenie damskie", 
        "koloryzacja", "cm", "sombre", "baleyage", "tonowanie", "botox", "regeneracja"
    ]
    if any(k in n for k in keywords):
        return True
        
    # Jeśli wariant zawiera w sobie jakieś kluczowe słowo z nazwy grupy
    words = [w for w in gn.replace("+", " ").replace("-", " ").split() if len(w) > 3]
    if any(w in n for w in words):
        return True
        
    return False

def parse():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    response = requests.get(BOOKSY_URL, headers=headers, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")
    
    lines = [clean(l) for l in soup.get_text("\n").splitlines() if clean(l)]
    
    try:
        start_idx = lines.index("Usługi")
    except ValueError:
        return {"error": "Nie znaleziono sekcji Usługi."}

    # Ucinamy sprawdzanie, gdy zaczynają się opinie lub stopka
    end_idx = len(lines)
    for idx in range(start_idx + 1, len(lines)):
        if lines[idx] in {"Opinie", "Udogodnienia", "Parking", "Godziny otwarcia"}:
            end_idx = idx
            break

    sec = lines[start_idx:end_idx]
    categories = []
    current_cat = None
    current_group = None

    def close_group():
        nonlocal current_group
        if current_group and current_cat:
            if current_group["variants"]:
                current_cat["items"].append(current_group)
            current_group = None

    i = 0
    while i < len(sec):
        line = sec[i]

        # 1. Tworzenie nowej kategorii
        if line in KNOWN_CATEGORIES or (i+1 < len(sec) and is_count(sec[i+1])):
            close_group()
            current_cat = {"name": line, "items": []}
            categories.append(current_cat)
            i += 2 if (i+1 < len(sec) and is_count(sec[i+1])) else 1
            continue

        if not current_cat:
            i += 1
            continue

        # 2. Główna usługa, pod którą będziemy chować warianty
        if line in GROUPABLE_SERVICES:
            close_group()
            current_group = {"name": line, "variants": []}
            i += 1
            continue

        # 3. Złapaliśmy przycisk "Umów" -> skanujemy w GÓRĘ, by przypisać dane do usługi
        if line == "Umów":
            dur, price, title = "", "", ""
            
            idx = i - 1
            while idx >= 0:
                s = sec[idx]
                if is_noise(s) or s == "Umów" or s in KNOWN_CATEGORIES or is_count(s):
                    pass # Omijamy śmieci reklamowe
                elif not dur and not price and is_dur(s):
                    dur = normalize_dur(s)
                elif not price and is_price(s):
                    price = extract_price(s)
                elif not is_dur(s) and not is_price(s):
                    title = s # Pierwszy czysty tekst nad ceną to nasza bezbłędna nazwa!
                    break
                idx -= 1
            
            if title and price:
                entry = {"name": title, "price": price, "duration": dur}
                
                # Zapisujemy wewnątrz wariantu lub jako samodzielna usługa
                if current_group:
                    if title == current_group["name"]:
                        close_group()
                        current_cat["items"].append(entry)
                    elif looks_like_variant(title, current_group["name"]):
                        current_group["variants"].append(entry)
                    else:
                        close_group()
                        current_cat["items"].append(entry)
                else:
                    current_cat["items"].append(entry)
        
        i += 1

    close_group()

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
    print(f"Sukces. Zapisano poprawnie w {OUT_PATH}")
