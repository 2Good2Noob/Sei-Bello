import json
import re
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict

import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

# Cena: "250,00 zł" albo "250,00 zł+"
PRICE_RE = re.compile(r"\b\d{1,4},\d{2}\s*zł\+?\b", re.IGNORECASE)

# Czas: "30min", "1g", "1g 30min", "1g30min"
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min|\d+\s*g\s*\d+\s*min|\d+g\d+min|\d+g)\b", re.IGNORECASE)

SKIP = {
    "Zarezerwuj", "Pokaż wszystkie zdjęcia", "Karty podarunkowe Booksy",
    "Więcej...", "Przedsiębiorca",
}

# Kategorie, które najczęściej występują (i które chcesz mieć jako accordion)
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

# Usługi, które mają warianty (Twoja lista + drobne normalizacje)
GROUPABLE_SERVICES = {
    "Strzyżenie damskie + mycie + stylizacja",
    "Koloryzacja włosów",
    "Farbowanie-odrost",
    "Metamorfoza koloru",
    "Przyciemnianie koloru blond+strzyżenie",
    "Tonowanie włosów + strzyżenie damskie",
    "Rozjaśnianie - odrost",
    "Rozjaśnianie – odrost",
    "Refleksy/sombre",
    "Dekoloryzacja włosów",
    "Baleyage",
    "Balayage",
    "Regeneracja włosów-botox",
    "Silna regeneracja włosów-PRO REPAIR COMBO+SPA",
    "Nanoplastia",
    "Trwała ondulacja",
}

# Linie, których nie wolno wrzucać jako "wariant tekstowy"
BAD_LABELS = {
    "Usługi", "Opinie", "Udogodnienia", "Parking", "Internet (Wi-Fi)", "Przyjazne dla dzieci",
}

def clean(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # ujednolicenie czasu typu "1g30min" -> "1g 30min"
    s = re.sub(r"(\d+)g(\d+)min", r"\1g \2min", s, flags=re.IGNORECASE)
    return s

def is_price(line: str) -> bool:
    return bool(PRICE_RE.search(line))

def is_duration(line: str) -> bool:
    return bool(DUR_RE.search(line))

def normalize_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def looks_like_variant_label(line: str, group_name: str) -> bool:
    if not line:
        return False
    if line in BAD_LABELS:
        return False
    if line in KNOWN_CATEGORIES:
        return False
    if re.fullmatch(r"\d+\s+usług[iy]?", line):
        return False
    if is_price(line) or is_duration(line) or line == "Umów":
        return False

    n = normalize_key(line)
    g = normalize_key(group_name)

    # typowe warianty
    if n.startswith(("włosy", "wlosy", "wariant")):
        return True

    # przypadki “pod-usług” w ramach jednej usługi
    if "strzyżenie damskie + mycie + stylizacja" in g:
        return n.startswith(("strzyżenie damskie -", "metamorfoza"))

    # jeżeli zawiera rdzeń nazwy grupy (np. farbowanie-odrost + strzyżenie)
    if g and g in n:
        return True

    # kilka “kluczowych” słów, które często są wariantami/usługami w ramach grupy
    if n.startswith(("metamorfoza", "farbowanie", "rozjaśnianie", "tonowanie", "trwała", "dekoloryzacja", "refleksy", "balayage", "baleyage")):
        return True

    return False

def find_section(lines: List[str]) -> List[str]:
    # start: "Usługi", a w pobliżu ma być "Popularne usługi"
    start = None
    for i, l in enumerate(lines):
        if l == "Usługi" and any("Popularne usługi" in x for x in lines[i:i + 200]):
            start = i
            break
    if start is None:
        raise RuntimeError("Nie znaleziono sekcji 'Usługi' na stronie Booksy.")

    # koniec: "Opinie"
    end = None
    for j in range(start + 1, len(lines)):
        if lines[j] == "Opinie":
            end = j
            break

    return lines[start:end or len(lines)]

def try_parse_card(sec: List[str], i: int) -> Tuple[Optional[Dict], int]:
    """
    Karta usługi ma w HTML zwykle:
    <nazwa> ... <cena> ... <czas> ... Umów
    Parsujemy tylko, jeśli w oknie jest 'Umów' (żeby nie przesuwać cen).
    """
    title = sec[i]
    if not title or title in SKIP:
        return None, i + 1
    if title in KNOWN_CATEGORIES or title in {"Usługi", "Opinie"}:
        return None, i + 1
    if re.fullmatch(r"\d+\s+usług[iy]?", title):
        return None, i + 1
    if is_price(title) or is_duration(title) or title == "Umów":
        return None, i + 1

    end = None
    for k in range(i + 1, min(i + 20, len(sec))):
        if sec[k] == "Umów":
            end = k
            break
    if end is None:
        return None, i + 1

    block = sec[i + 1:end]
    price = ""
    dur = ""

    # cena = pierwsza pasująca
    for b in block:
        m = PRICE_RE.search(b)
        if m:
            price = clean(m.group(0))
            break

    # czas = pierwsza pasująca
    for b in block:
        if is_duration(b):
            m = DUR_RE.search(b)
            if m:
                dur = clean(m.group(0))
                break

    if not price and not dur:
        # karta bez danych -> traktuj jako nie-karta
        return None, i + 1

    return {"name": clean(title), "price": price, "duration": dur}, end + 1

def parse():
    r = requests.get(
        BOOKSY_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SeiBelloPriceBot/2.0)"},
        timeout=30,
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    lines = [clean(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x and x not in SKIP]

    sec = find_section(lines)

    categories: List[Dict] = []
    current_cat: Optional[Dict] = None

    def start_category(name: str):
        nonlocal current_cat
        current_cat = {"name": name, "items": []}
        categories.append(current_cat)

    def ensure_category():
        if current_cat is None:
            start_category("Cennik")

    i = 0
    current_group: Optional[Dict] = None  # {"name","price","duration","variants":[]}

    def flush_group():
        nonlocal current_group
        if current_group and current_cat:
            # usuń puste/dublujące warianty
            seen = set()
            cleaned = []
            for v in current_group.get("variants", []):
                key = normalize_key(v.get("name", ""))
                if not key:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                cleaned.append(v)
            current_group["variants"] = cleaned

            current_cat["items"].append(current_group)
        current_group = None

    while i < len(sec):
        line = sec[i]

        # nowa kategoria: "Usługi męskie" + "4 usługi" albo znana nazwa kategorii
        if (i + 1 < len(sec) and re.fullmatch(r"\d+\s+usług[iy]?", sec[i + 1]) and line not in {"Usługi"}):
            flush_group()
            start_category(line)
            i += 2
            continue

        if line in KNOWN_CATEGORIES:
            flush_group()
            start_category(line)
            i += 1
            continue

        ensure_category()

        card, next_i = try_parse_card(sec, i)
        if card:
            name = card["name"]

            # jeśli weszła nowa usługa "groupable" -> zamknij poprzednią grupę i startuj nową
            if name in GROUPABLE_SERVICES:
                flush_group()
                current_group = {
                    "name": name,
                    "price": card.get("price", ""),
                    "duration": card.get("duration", ""),
                    "variants": []
                }
                i = next_i
                continue

            # jeśli jesteśmy w grupie:
            if current_group:
                # jeśli trafiliśmy na kolejną groupable usługę (a nie złapaliśmy jej jako card powyżej) – zabezpieczenie
                if name in GROUPABLE_SERVICES and name != current_group["name"]:
                    flush_group()
                    current_group = {
                        "name": name,
                        "price": card.get("price", ""),
                        "duration": card.get("duration", ""),
                        "variants": []
                    }
                    i = next_i
                    continue

                # czy to wariant dla aktualnej grupy?
                if looks_like_variant_label(name, current_group["name"]) or name == current_group["name"]:
                    current_group["variants"].append(card)
                    i = next_i
                    continue

                # inaczej: koniec grupy, a ten wpis to normalna usługa
                flush_group()
                current_cat["items"].append(card)
                i = next_i
                continue

            # normalna usługa bez grupy
            current_cat["items"].append(card)
            i = next_i
            continue

        # jeżeli nie jest kartą, a jesteśmy w grupie — możliwy “tekstowy wariant” bez ceny
        if current_group and looks_like_variant_label(line, current_group["name"]):
            current_group["variants"].append({"name": line, "price": "", "duration": ""})

        i += 1

    flush_group()

    payload = {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": [c for c in categories if c.get("items")],
    }
    return payload

if __name__ == "__main__":
    data = parse()
    import os
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = 0
    for c in data["categories"]:
        for it in c["items"]:
            total += 1
            if isinstance(it, dict) and it.get("variants"):
                total += len(it["variants"])
    print(f"OK -> {OUT_PATH} (wpisów/wariantów: {total})")
