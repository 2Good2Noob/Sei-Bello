import json
import re
import os
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

PRICE_RE = re.compile(r"\b\d{1,4},\d{2}\s*zł\+?\b", re.IGNORECASE)
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min)\b", re.IGNORECASE)
COUNT_RE = re.compile(r"^\d+\s+usług[iy]?$", re.IGNORECASE)

KNOWN_CATEGORIES = {
    "Popularne usługi",
    "Usługi męskie",
    "Usługi damskie",
    "Koloryzacja",
    "Zabiegi regenerujące włosy",
    "Prostowanie",
    "Fryzura okolicznościowa",
    "Fryzury okolicznościowe",
    "Trwała ondulacja",
}

# Usługi, które grupują warianty pod sobą
VARIANT_SERVICES = {
    "Strzyżenie damskie + mycie + stylizacja",
    "Koloryzacja włosów",
    "Farbowanie-odrost",
    "Metamorfoza koloru",
    "Przyciemnianie koloru blond+strzyżenie",
    "Tonowanie włosów + strzyżenie damskie",
    "Rozjaśnianie - odrost",
    "Refleksy/sombre",
    "Dekoloryzacja włosów",
    "Baleyage",
    "Regeneracja włosów-botox",
    "Silna regeneracja włosów-PRO REPAIR COMBO+SPA",
    "Nanoplastia",
    "Trwała ondulacja",
}

SKIP_EXACT = {
    "Umów", "Zarezerwuj", "Zapisz termin", "Pokaż wszystkie zdjęcia",
    "* * *", "Udogodnienia", "Parking", "Internet (Wi-Fi)", "Przyjazne dla dzieci",
}

def clean(s: str) -> str:
    s = s.replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()

def should_skip(line: str) -> bool:
    if not line or line in SKIP_EXACT: return True
    low = line.lower()
    return any(x in low for x in ["portfolio usługi", "image:", "booksy logo"])

def is_price(line: str) -> bool: return bool(PRICE_RE.search(line))
def is_duration(line: str) -> bool: return bool(DUR_RE.search(line))
def is_count(line: str) -> bool: return bool(COUNT_RE.match(line))

def normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("—", "-")).strip()

def find_section(lines: list[str]) -> list[str]:
    try:
        start = lines.index("Usługi")
        end = lines.index("Opinie") if "Opinie" in lines else len(lines)
        sec = lines[start:end]
        return sec[:sec.index("Udogodnienia")] if "Udogodnienia" in sec else sec
    except ValueError:
        return lines

def extract_price(line: str) -> str:
    prices = PRICE_RE.findall(line)
    return clean(prices[-1]) if prices else ""

def is_variant_of(parent: str, cand: str) -> bool:
    p = normalize_name(parent)
    c = normalize_name(cand)
    # Słowa kluczowe wskazujące na wariant (długość, typ włosów, metamorfoza)
    keywords = ["włosy", "wlosy", "krótkie", "średnie", "długie", "ramion", "gęste", "metamorfoza", "odrost", "tonowanie"]
    return any(k in c for x in keywords for k in [x])

def parse():
    r = requests.get(BOOKSY_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    
    lines = [clean(x) for x in soup.get_text("\n").splitlines() if not should_skip(clean(x))]
    sec = find_section(lines)

    categories = []
    current_cat = None
    i = 0

    while i < len(sec):
        line = sec[i]

        if line in KNOWN_CATEGORIES or (i+1 < len(sec) and is_count(sec[i+1])):
            current_cat = {"name": line, "items": []}
            categories.append(current_cat)
            i += (2 if i+1 < len(sec) and is_count(sec[i+1]) else 1)
            continue

        if not current_cat:
            i += 1
            continue

        if is_price(line) or is_duration(line):
            i += 1
            continue

        # Parsowanie usługi
        service_name = line
        variants = []
        j = i + 1

        # Sprawdzanie czy następne linie to warianty
        while j < len(sec):
            if j < len(sec) and (sec[j] in KNOWN_CATEGORIES or is_count(sec[j])):
                break
            
            if is_variant_of(service_name, sec[j]) and not is_price(sec[j]) and not is_duration(sec[j]):
                v_name = sec[j]
                v_price = ""
                v_dur = ""
                k = j + 1
                # Szukaj ceny i czasu dla tego wariantu
                while k < len(sec) and k < j + 4:
                    if is_price(sec[k]): v_price = extract_price(sec[k])
                    elif is_duration(sec[k]): v_dur = sec[k]
                    if v_price and v_dur: break
                    if not is_price(sec[k]) and not is_duration(sec[k]) and k > j + 1: break
                    k += 1
                
                variants.append({"name": v_name, "price": v_price, "duration": v_dur})
                j = k
            else:
                break

        if variants:
            current_cat["items"].append({"name": service_name, "variants": variants})
            i = j
        else:
            # Zwykła usługa bez wariantów
            price = ""
            dur = ""
            k = i + 1
            while k < len(sec) and k < i + 4:
                if is_price(sec[k]): price = extract_price(sec[k])
                elif is_duration(sec[k]): dur = sec[k]
                k += 1
            current_cat["items"].append({"name": service_name, "price": price, "duration": dur})
            i = k

    return {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": [c for c in categories if c["items"]]
    }

if __name__ == "__main__":
    data = parse()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Zapisano pomyślnie do {OUT_PATH}")
