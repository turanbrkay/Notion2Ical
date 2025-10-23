# generate_ics.py
# Çalıştırmak için: NOTION_TOKEN ve NOTION_DATABASE_ID ortam değişkenlerini ver,
# "docs/calendar.ics" dosyasını üretir.
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple
from notion_client import Client as NotionClient

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ["NOTION_DATABASE_ID"]
FEED_NAME    = os.getenv("FEED_NAME", "Flashcards")
FEED_TZ      = os.getenv("FEED_TZ", "Europe/Istanbul")

notion = NotionClient(
    auth=NOTION_TOKEN,
    notion_version="2022-06-28"   # stabil, eski endpoint sözleşmesi
)

def iter_notion_pages(database_id: str, page_size: int = 100):
    next_cursor = None
    while True:
        resp = notion.databases.query(database_id=database_id, page_size=page_size, start_cursor=next_cursor)
        for row in resp.get("results", []):
            yield row
        if not resp.get("has_more"):
            break
        next_cursor = resp.get("next_cursor")

def rich_text_to_plain(prop_obj: Dict[str, Any]) -> str:
    rich = prop_obj.get("title") or prop_obj.get("rich_text")
    if not rich:
        return ""
    return "".join(t.get("plain_text", "") for t in rich)

def find_title_prop_obj(props: Dict[str, Any]) -> Dict[str, Any]:
    for _, obj in props.items():
        if obj.get("type") == "title":
            return obj
    return {}

def find_date_prop_obj(props: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("Unified Date", "Next Repetition", "Repetition Date"):
        if key in props and props[key].get("type") == "date":
            return props[key]
    for _, obj in props.items():
        if obj.get("type") == "date":
            return obj
    return {}

def extract_date_range(prop_obj: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], bool]:
    date = prop_obj.get("date")
    if not date:
        return None, None, False
    start = date.get("start")
    end   = date.get("end")
    is_dt = "T" in start if start else False
    return start, end, is_dt

def iso_to_ical_dt(val: str, to_utc: bool) -> str:
    if not to_utc:
        y, m, d = val.split("-")
        return f"{y}{m}{d}"  # DATE
    dt = datetime.fromisoformat(val.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")

def ics_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")

def page_to_vevent(page: Dict[str, Any]) -> Optional[str]:
    props = page.get("properties", {})
    title_prop = find_title_prop_obj(props)
    date_prop  = find_date_prop_obj(props)

    title = rich_text_to_plain(title_prop).strip() or "Untitled"
    start_iso, end_iso, is_datetime = extract_date_range(date_prop)
    if not start_iso:
        return None

    if is_datetime:
        start_ical = iso_to_ical_dt(start_iso, True)
        end_ical   = iso_to_ical_dt(end_iso, True) if end_iso else start_ical
        dt_start = f"DTSTART:{start_ical}"
        dt_end   = f"DTEND:{end_ical}"
    else:
        start_ical = iso_to_ical_dt(start_iso, False)
        if end_iso:
            end_ical = iso_to_ical_dt(end_iso, False)
        else:
            y, m, d = start_ical[:4], start_ical[4:6], start_ical[6:8]
            plus1 = (datetime(int(y), int(m), int(d)) + timedelta(days=1)).strftime("%Y%m%d")
            end_ical = plus1
        dt_start = f"DTSTART;VALUE=DATE:{start_ical}"
        dt_end   = f"DTEND;VALUE=DATE:{end_ical}"

    lines = [
        "BEGIN:VEVENT",
        f"UID:{page['id']}-notion@ics",
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        dt_start,
        dt_end,
        f"SUMMARY:{ics_escape(title)}",
    ]
    if page.get("url"):
        lines.append(f"URL:{ics_escape(page['url'])}")
    lines.append(f"X-NOTION-PAGE-ID:{page['id']}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)

def generate_ics() -> str:
    head = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Notion ICS Bridge//EN",
        f"X-WR-CALNAME:{ics_escape(FEED_NAME)}",
        f"X-WR-TIMEZONE:{ics_escape(FEED_TZ)}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    events = []
    for page in iter_notion_pages(NOTION_DB_ID):
        ve = page_to_vevent(page)
        if ve:
            events.append(ve)
    tail = ["END:VCALENDAR"]
    return "\r\n".join(head + events + tail) + "\r\n"

if __name__ == "__main__":
    ics = generate_ics()
    os.makedirs("docs", exist_ok=True)
    # CRLF satır sonu; iCalendar bunu sever.
    with open("docs/calendar.ics", "w", encoding="utf-8", newline="\r\n") as f:
        f.write(ics)
    print("Wrote docs/calendar.ics")
