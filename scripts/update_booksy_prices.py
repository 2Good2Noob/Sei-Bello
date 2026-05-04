import json
import os
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

# UWAGA: bez końcowego \b, żeby poprawnie łapać "zł+"
PRICE_RE = re.compile(r"\b\d{1,4},\d{2}\s*zł\+?(?=\s|$)", re.IGNORECASE)

# Łapie: "2g", "1g30min", "1g 30min", "30min"
DUR_RE = re.compile(r"\b\d+\s*g(?:\s*\d+\s*min)?\b|\b\d+\s*min\b", re.IGNORECASE)

COUNT_RE = re.compile(r"^\d+\s+usług[iy]?$", re.IGNORECASE)

SKIP_EXACT = {
    "Zarezerwuj",
    "Zapisz termin",
    "Pokaż wszystkie zdjęcia",
    "Szukaj usługi",
}

STOP_MARKERS = {
    "Opinie",
    "Udogodnienia",
    "Godziny otwarcia",
    "Mapa",
}


def clean(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_price(line: str) -> bool:
    return bool(PRICE_RE.search(line))


def is_duration(line: str) -> bool:
    return bool(DUR_RE.search(line))


def normalize_duration(s: str) -> str:
    s = clean(s)
    # "1g30min" -> "1g 30min"
    s = re.sub(r"(\d)\s*g\s*(\d)", r"\1g \2", s, flags=re.IGNORECASE)
    # "30 min" -> "30min"
    s = s.replace(" min", "min")
    return s


def extract_price(line: str) -> str:
    found = PRICE_RE.findall(line)
    return clean(found[-1]) if found else clean(line)


def token0(s: str) -> str:
    # pierwszy "token" porównawczy (żeby odróżniać warianty od nowych usług)
    s = re.sub(r"[^\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", " ", s, flags=re.IGNORECASE)
    s = clean(s).lower()
    return s.split()[0] if s else ""


def is_description_line(line: str) -> bool:
    l = clean(line)
    low = l.lower()
    if not l:
        return True
    if l.startswith("Image:") or "Portfolio usługi" in l:
        return True
    if "..." in l:
        return True
    if "." in l:
        return True
    if low.startswith(("w cen", "w cene", "w cenę", "w cenie")):
        return True
    return False


def is_probably_variant(name: str, parent_name: str) -> bool:
    n = clean(name).lower()
    if n.startswith(("włosy", "wlosy", "wariant", "cena od", "od ")):
        return True

    p0 = token0(parent_name)
    n0 = token0(name)
    if p0 and n0 and p0 == n0:
        return True

    return False


def find_section(lines: list[str]) -> list[str]:
    start = None
    for i, l in enumerate(lines):
        if l == "Usługi":
            start = i
            break
    if start is None:
        raise RuntimeError("Nie znaleziono sekcji 'Usługi' na stronie Booksy.")

    end = None
    for j in range(start + 1, len(lines)):
        if lines[j] in STOP_MARKERS:
            end = j
            break

    return lines[start : (end if end is not None else len(lines))]


def parse_price_block(sec: list[str], i: int, category_headers: set[str]):
    """
    Parsuje blok:
      nazwa
      (opcjonalnie jakieś śmieci)
      cena
      czas
      Umów
    Zabezpieczenie: jeśli po nazwie pojawia się linia Image:/Portfolio -> nie parsujemy
    (bo to najczęściej start kolejnej usługi).
    """
    name = sec[i]
    if (
        name in category_headers
        or COUNT_RE.match(name or "")
        or is_price(name)
        or is_duration(name)
        or clean(name).lower() == "umów"
    ):
        return None

    price_idx = None
    for k in range(i + 1, min(i + 8, len(sec))):
        nxt = sec[k]
        if nxt.startswith("Image:") or "Portfolio usługi" in nxt:
            return None
        if nxt in category_headers or (k + 1 < len(sec) and COUNT_RE.match(sec[k + 1] or "")):
            return None
        if is_price(nxt):
            price_idx = k
            break

    if price_idx is None:
        return None

    dur_idx = None
    for k in range(price_idx + 1, min(price_idx + 6, len(sec))):
        if is_duration(sec[k]):
            dur_idx = k
            break

    umow_idx = None
    for k in range((dur_idx or price_idx) + 1, min((dur_idx or price_idx) + 6, len(sec))):
        if clean(sec[k]).lower() == "umów":
            umow_idx = k
            break
    if umow_idx is None:
        umow_idx = dur_idx or price_idx

    item = {
        "name": clean(name),
        "price": extract_price(sec[price_idx]),
        "duration": normalize_duration(sec[dur_idx]) if dur_idx is not None else "",
    }
    return item, umow_idx + 1


def parse_base(sec: list[str], i: int):
    """
    Bazowy blok ceny dla usługi (często to 'Cena od' / pierwszy wariant).
    Po nazwie mogą być 1..N linii Image: Portfolio...
    """
    j = i + 1
    while j < len(sec) and (sec[j].startswith("Image:") or "Portfolio usługi" in sec[j]):
        j += 1

    price_idx = None
    for k in range(j, min(j + 10, len(sec))):
        if is_price(sec[k]):
            price_idx = k
            break
    if price_idx is None:
        return None

    dur_idx = None
    for k in range(price_idx + 1, min(price_idx + 8, len(sec))):
        if is_duration(sec[k]):
            dur_idx = k
            break

    umow_idx = None
    for k in range((dur_idx or price_idx) + 1, min((dur_idx or price_idx) + 8, len(sec))):
        if clean(sec[k]).lower() == "umów":
            umow_idx = k
            break
    if umow_idx is None:
        umow_idx = dur_idx or price_idx

    return {
        "price": extract_price(sec[price_idx]),
        "duration": normalize_duration(sec[dur_idx]) if dur_idx is not None else "",
        "next_i": umow_idx + 1,
    }


def parse_service(sec: list[str], i: int, category_headers: set[str]):
    """
    Usługa może być:
      A) prosta: name + price + dur
      B) nagłówek + warianty (często ostatni wariant jest tylko nazwą bez ceny) => robimy SHIFT
    """
    name = sec[i]
    base = parse_base(sec, i)
    if not base:
        return None

    base_price = base["price"]
    base_dur = base["duration"]
    k = base["next_i"]

    variant_blocks = []
    trailing_variant_names = []

    while k < len(sec):
        line = sec[k]

        # koniec kategorii / sekcji
        if line in category_headers or (k + 1 < len(sec) and COUNT_RE.match(sec[k + 1] or "")):
            break

        # start kolejnej "dużej" usługi często ma Image: w następnej linii
        if k + 1 < len(sec) and (sec[k + 1].startswith("Image:") or "Portfolio usługi" in sec[k + 1]):
            break

        if is_price(line) or is_duration(line) or clean(line).lower() == "umów":
            k += 1
            continue

        # spróbuj sparsować kolejny blok cena/czas
        pb = parse_price_block(sec, k, category_headers)
        if pb:
            item, next_k = pb
            # jeśli to nie wygląda na wariant tej usługi -> to pewnie nowa usługa (np. Farbowanie-odrost)
            if not is_probably_variant(item["name"], name):
                break
            variant_blocks.append(item)
            k = next_k
            continue

        # brak ceny => może to być ostatni wariant (tylko nazwa) albo opis
        if not is_description_line(line) and clean(line) not in SKIP_EXACT:
            trailing_variant_names.append(clean(line))

        k += 1

    # bez wariantów
    if not variant_blocks and not trailing_variant_names:
        return {"item": {"name": clean(name), "price": base_price, "duration": base_dur}, "next_i": k}

    # mamy warianty => budujemy item z variants[]
    # SHIFT: jeśli mamy trailing nazwę bez ceny, to:
    #   variants[0] dostaje bazową cenę/czas,
    #   variants[1] dostaje cenę/czas z pierwszego variant_block,
    #   ...
    names = [vb["name"] for vb in variant_blocks] + trailing_variant_names
    variants = []

    if trailing_variant_names and names:
        variants.append({"name": names[0], "price": base_price, "duration": base_dur})
        for idx in range(1, len(names)):
            src = variant_blocks[min(idx - 1, len(variant_blocks) - 1)]
            variants.append({"name": names[idx], "price": src.get("price", ""), "duration": src.get("duration", "")})
    else:
        # brak trailing => zostaw bazową jako "Cena od" jeśli nie jest duplikatem pierwszego wariantu
        if variant_blocks and (base_price != variant_blocks[0].get("price") or base_dur != variant_blocks[0].get("duration")):
            variants.append({"name": "Cena od", "price": base_price, "duration": base_dur})
        variants.extend(variant_blocks)

    return {"item": {"name": clean(name), "variants": variants}, "next_i": k}


def parse():
    r = requests.get(
        BOOKSY_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SeiBelloPriceBot/1.0)"},
        timeout=30,
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    lines = [clean(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x and x not in SKIP_EXACT]

    sec = find_section(lines)

    # wykryj nagłówki kategorii (np. "Usługi męskie" + "4 usługi")
    category_headers = set()
    for i in range(len(sec) - 1):
        if COUNT_RE.match(sec[i + 1] or ""):
            category_headers.add(sec[i])
    category_headers.add("Popularne usługi")  # ta zwykle nie ma licznika obok

    categories = []
    current = None

    def start_category(name: str):
        nonlocal current
        current = {"name": clean(name), "items": []}
        categories.append(current)

    i = 0
    while i < len(sec):
        line = sec[i]

        if line in STOP_MARKERS:
            break

        # start kategorii
        if line in category_headers:
            start_category(line)
            # jeśli za kategorią jest "X usługi" -> przeskocz licznik
            if i + 1 < len(sec) and COUNT_RE.match(sec[i + 1] or ""):
                i += 2
            else:
                i += 1
            continue

        if current is None:
            i += 1
            continue

        # pomijamy śmieci/obrazy
        if line.startswith("Image:") or "Portfolio usługi" in line:
            i += 1
            continue

        # próba parsowania usługi
        parsed = parse_service(sec, i, category_headers)
        if parsed:
            current["items"].append(parsed["item"])
            i = parsed["next_i"]
            continue

        i += 1

    payload = {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": [c for c in categories if c.get("items")],
    }
    return payload


if __name__ == "__main__":
    data = parse()
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = 0
    for c in data["categories"]:
        for it in c["items"]:
            total += len(it.get("variants", [])) if "variants" in it else 1

    print(f"OK -> {OUT_PATH} ({total} pozycji)")
