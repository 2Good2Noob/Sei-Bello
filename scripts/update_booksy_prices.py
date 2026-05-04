import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

# ceny typu: 250,00 zł lub 250,00 zł+
PRICE_RE = re.compile(r"(\d{1,4},\d{2})\s*zł(\+)?", re.IGNORECASE)

# czasy typu: 30min, 1g, 1g 30min, 1g30min
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min)\b", re.IGNORECASE)

# rzeczy do wywalenia z tekstu
SKIP_EXACT = {
    "Zapisz termin",
    "Pokaż wszystkie zdjęcia",
    "Zarezerwuj",
    "Zarezerwuj wizytę",
}

# kiedy kończyć sekcję usług (żeby nie wciągać „Udogodnień” itd.)
STOP_AT = {"Opinie", "Udogodnienia"}

# kategorie (nagłówki) – część wykrywa się też po linijce „X usług”
KNOWN_CATEGORIES = {
    "Popularne usługi",
    "Usługi męskie",
    "Usługi damskie",
    "Koloryzacja",
    "Zabiegi regenerujące włosy",
    "Prostowanie",
    "Fryzura okolicznościowa",
    "Trwała ondulacja",
}

# usługi, które na Booksy mają warianty (i chcemy je grupować)
GROUPABLE_SERVICES = {
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

def clean(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def norm_key(s: str) -> str:
    return clean(s).lower()

def is_price_line(s: str) -> bool:
    return bool(PRICE_RE.search(s))

def extract_price(s: str) -> Optional[str]:
    m = PRICE_RE.search(s)
    if not m:
        return None
    val = m.group(1)
    plus = m.group(2)
    return f"{val} zł{plus or ''}"

def is_duration_line(s: str) -> bool:
    return bool(DUR_RE.search(s))

def normalize_duration(s: str) -> str:
    if not s:
        return ""
    m = DUR_RE.search(s)
    if not m:
        return clean(s)
    d = m.group(1).lower().replace(" ", "")
    # 1g30min -> 1g 30min
    d = re.sub(r"(\d+)g(\d+)min", r"\1g \2min", d)
    # 30min już ok
    return d

def is_count_line(s: str) -> bool:
    return bool(re.fullmatch(r"\d+\s+usług[iy]?", s))

def is_noise_line(s: str) -> bool:
    if not s:
        return True
    if s in SKIP_EXACT:
        return True
    # alt-y obrazków / inne śmieci
    if s.lower().startswith("portfolio usługi"):
        return True
    if s.lower().startswith("image: portfolio usługi"):
        return True
    return False

def looks_like_hair_variant(name: str) -> bool:
    n = norm_key(name)
    return n.startswith("włosy") or n.startswith("wlosy") or n.startswith("wariant") or ("włosy" in n) or ("wlosy" in n)

def find_section(lines: List[str]) -> List[str]:
    # zacznij od pierwszego „Usługi”
    start = None
    for i, l in enumerate(lines):
        if l == "Usługi":
            start = i
            break
    if start is None:
        raise RuntimeError("Nie znaleziono sekcji 'Usługi' na stronie Booksy.")

    # utnij na pierwszym „Opinie” lub „Udogodnienia” (cokolwiek będzie wcześniej)
    end = None
    for j in range(start + 1, len(lines)):
        if lines[j] in STOP_AT:
            end = j
            break

    return lines[start : (end or len(lines))]

def split_categories(sec: List[str]) -> List[Tuple[str, int, int]]:
    # zwraca listę (cat_name, start_idx_after_header, end_idx)
    starts: List[Tuple[str, int]] = []
    i = 0
    while i < len(sec):
        line = sec[i]
        if line in STOP_AT:
            break

        if line in KNOWN_CATEGORIES:
            j = i + 1
            # pomiń linijkę typu „4 usługi”
            if j < len(sec) and is_count_line(sec[j]):
                j += 1
            starts.append((line, j))
            i = j
            continue

        # wykrycie kategorii po schemacie: "<nazwa>" + "<X usług>"
        if i + 1 < len(sec) and is_count_line(sec[i + 1]):
            name = line
            j = i + 2
            starts.append((name, j))
            i = j
            continue

        i += 1

    out: List[Tuple[str, int, int]] = []
    for idx, (name, s) in enumerate(starts):
        e = starts[idx + 1][1] - 1 if idx + 1 < len(starts) else len(sec)
        out.append((name, s, e))
    return out

def try_parse_bookable_entry(lines: List[str], i: int) -> Optional[Tuple[Dict[str, Any], int]]:
    """
    Próbuje sparsować blok:
      <TYTUŁ>
      [różne śmieci / obrazy / itp]
      <CENA>
      <CZAS>
      Umów
    Zabezpieczenie: jeśli zanim znajdziemy jakąkolwiek cenę pojawi się kolejny „tytuł”,
    to uznajemy, że to był tylko „label” (wariant bez ceny) i NIE zjadamy indeksów.
    """
    title = lines[i]
    if is_noise_line(title) or title == "Umów" or is_price_line(title) or is_duration_line(title) or is_count_line(title):
        return None

    price: Optional[str] = None
    dur: str = ""

    j = i + 1
    # mały limit – żeby nie „przeskoczyć” do kolejnej usługi i nie przesunąć cen
    limit = min(i + 14, len(lines) - 1)

    while j <= limit:
        s = lines[j]

        if s in STOP_AT or s in KNOWN_CATEGORIES:
            return None

        if is_noise_line(s):
            j += 1
            continue

        # kolejny potencjalny tytuł zanim złapiemy cenę => poprzedni to label
        if price is None and s not in {"Umów"} and not is_price_line(s) and not is_duration_line(s) and not is_count_line(s):
            # UWAGA: to heurystyka, działa dobrze dla Booksy, bo cena jest blisko tytułu
            return None

        if price is None and is_price_line(s):
            price = extract_price(s)

        if not dur and is_duration_line(s):
            dur = normalize_duration(s)

        if s == "Umów":
            if price:  # „Umów” bez ceny -> nie uznajemy za poprawny blok
                entry = {"name": clean(title), "price": price, "duration": dur or ""}
                return entry, j + 1
            return None

        j += 1

    return None

def parse_category(lines: List[str]) -> List[Dict[str, Any]]:
    items_out: List[Dict[str, Any]] = []

    current_group: Optional[Dict[str, Any]] = None
    base_price: Optional[str] = None
    base_dur: str = ""
    base_label_used: bool = False

    def flush_group():
        nonlocal current_group, base_price, base_dur, base_label_used
        if not current_group:
            return

        # jeśli udało się przypisać cenę bazową do konkretnego wariantu (np. "Włosy długie"),
        # usuń "Cena od"
        if base_label_used:
            current_group["variants"] = [
                v for v in current_group["variants"]
                if norm_key(v.get("name", "")) != "cena od"
            ]

        # wywal warianty całkiem puste
        current_group["variants"] = [
            v for v in current_group["variants"]
            if v.get("price") or v.get("duration")
        ]

        if current_group["variants"]:
            items_out.append(current_group)
        else:
            # awaryjnie: jeśli brak wariantów, pokaż usługę jako prostą pozycję (jeśli miała cenę)
            if base_price:
                items_out.append({"name": current_group["name"], "price": base_price, "duration": base_dur})

        current_group = None
        base_price = None
        base_dur = ""
        base_label_used = False

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or is_noise_line(line):
            i += 1
            continue
        if line in STOP_AT:
            break
        if line == "Umów" or is_count_line(line):
            i += 1
            continue

        parsed = try_parse_bookable_entry(lines, i)

        if parsed:
            entry, next_i = parsed
            title = entry["name"]

            # start / continue group?
            if current_group:
                # jeżeli to wygląda na wariant (np. "Włosy ..." albo zawiera "włosy")
                if looks_like_hair_variant(title):
                    current_group["variants"].append(entry)
                    i = next_i
                    continue

                # nowa usługa -> zamykamy grupę
                flush_group()

            # czy ta usługa ma być grupowana?
            if title in GROUPABLE_SERVICES:
                current_group = {"name": title, "variants": []}
                base_price = entry.get("price")
                base_dur = entry.get("duration", "") or ""
                # domyślnie dajemy "Cena od", chyba że później pojawi się np. "Włosy długie" bez ceny
                if base_price:
                    current_group["variants"].append({"name": "Cena od", "price": base_price, "duration": base_dur})
                i = next_i
                continue

            # zwykła pozycja
            items_out.append(entry)
            i = next_i
            continue

        # jeśli nie udało się sparsować jako bookable entry -> to prawie na pewno label wariantu bez ceny
        label = clean(line)

        if current_group and base_price:
            # Jeśli label wygląda jak wariant włosów, przypisz mu cenę bazową
            if looks_like_hair_variant(label):
                current_group["variants"].append({"name": label, "price": base_price, "duration": base_dur})
                base_label_used = True
            else:
                # nie-hair label (np. "+ strzyżenie") – zostaw jako "Zapytaj" (null), żeby nie mieszać cen
                current_group["variants"].append({"name": label, "price": None, "duration": None})

        i += 1

    # koniec kategorii
    flush_group()
    return items_out

def parse() -> Dict[str, Any]:
    r = requests.get(
        BOOKSY_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SeiBelloPriceBot/1.0)"},
        timeout=30,
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    raw_lines = [clean(x) for x in soup.get_text("\n").splitlines()]
    # nie usuwamy "Umów" – to kluczowy separator
    lines = [x for x in raw_lines if x and x not in SKIP_EXACT]

    sec = find_section(lines)

    categories_out: List[Dict[str, Any]] = []
    for cat_name, s, e in split_categories(sec):
        cat_lines = sec[s:e]
        items = parse_category(cat_lines)
        if items:
            categories_out.append({"name": cat_name, "items": items})

    payload = {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": categories_out,
    }
    return payload

if __name__ == "__main__":
    data = parse()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    total = 0
    for c in data["categories"]:
        for it in c["items"]:
            if "variants" in it:
                total += len(it["variants"])
            else:
                total += 1
    print(f"OK -> {OUT_PATH} ({total} pozycji/wierszy)")
