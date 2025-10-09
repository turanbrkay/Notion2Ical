# app.py
# pip install flask python-dotenv notion-client
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple
from flask import make_response
from flask import Flask, Response, make_response, request

from dotenv import load_dotenv
from notion_client import Client as NotionClient

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DB_ID = os.getenv("NOTION_DATABASE_ID")
FEED_NAME = os.getenv("FEED_NAME", "Notion Feed")
FEED_TZ = os.getenv("FEED_TZ", "Europe/Istanbul")

if not NOTION_TOKEN or not NOTION_DB_ID:
    raise SystemExit("NOTION_TOKEN ve NOTION_DATABASE_ID zorunludur (.env).")

notion = NotionClient(auth=NOTION_TOKEN)
app = Flask(__name__)

def iter_notion_pages(database_id: str, page_size: int = 100):
    next_cursor = None
    while True:
        resp = notion.databases.query(
            database_id=database_id,
            page_size=page_size,
            start_cursor=next_cursor
        )
        for row in resp.get("results", []):
            yield row
        if not resp.get("has_more"):
            break
        next_cursor = resp.get("next_cursor")

def rich_text_to_plain(prop_obj: Dict[str, Any]) -> str:
    rich = prop_obj.get("title") or prop_obj.get("rich_text")
    if not rich:
        return ""
    return "".join([t.get("plain_text", "") for t in rich])

# --- basit cache ---
CACHE_TTL_SECONDS = 600  # 10 dakika; istersen 300/900 yapabilirsin
_CACHE_FULL = {"at": None, "data": None}
_CACHE_LITE = {"at": None, "data": None}

def cached_ics(limit: Optional[int]):
    now = datetime.now(timezone.utc)
    bucket = _CACHE_LITE if (limit is not None) else _CACHE_FULL
    if bucket["at"] and (now - bucket["at"]).total_seconds() < CACHE_TTL_SECONDS and bucket["data"]:
        return bucket["data"]
    data = generate_ics(limit=limit)
    bucket["at"], bucket["data"] = now, data
    return data


def find_title_prop_obj(props: Dict[str, Any]) -> Dict[str, Any]:
    for _, obj in props.items():
        if obj.get("type") == "title":
            return obj
    return {}

def find_date_prop_obj(props: Dict[str, Any]) -> Dict[str, Any]:
    # BU YANLIŞTI: ("Unified Date")  -> karakter karakter gezer
    # for key in ("Unified Date"):

    for key in ("Unified Date", "Next Repetition", "Repetition Date"):
        if key in props and props[key].get("type") == "date":
            return props[key]
    for _, obj in props.items():
        if obj.get("type") == "date":
            return obj
    return {}


def extract_date_range(prop_obj: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], bool]:
    """
    return: (start_iso, end_iso, is_datetime)
    """
    date = prop_obj.get("date")
    if not date:
        return None, None, False
    start = date.get("start")
    end = date.get("end")
    is_dt = "T" in start if start else False
    return start, end, is_dt

def parse_iso_local(val: str) -> datetime:
    """
    Notion'un ISO 8601 tarihlerini (Z veya +03:00'lü) aware datetime'a çevirir.
    Örn: "2025-10-09T00:09:00.000+03:00" → datetime(..., tzinfo=UTC+3)
    """
    # 'Z' (UTC) geldiğinde fromisoformat anlayabilsin diye '+00:00' ile değiştiriyoruz
    return datetime.fromisoformat(val.replace("Z", "+00:00"))


def iso_to_ical_dt(val: str, to_utc: bool) -> str:
    if not to_utc:
        y, m, d = val.split("-")
        return f"{y}{m}{d}"  # DATE
    dt = datetime.fromisoformat(val.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")

def ics_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")

def notion_page_to_vevent(page: Dict[str, Any]) -> Optional[str]:
    props = page.get("properties", {})

    title_prop = find_title_prop_obj(props)
    date_prop  = find_date_prop_obj(props)
    desc_prop  = props.get("Description", {})
    loc_prop   = props.get("Location", {})

    title = rich_text_to_plain(title_prop).strip() or "Untitled"
    start_iso, end_iso, is_datetime = extract_date_range(date_prop)
    if not start_iso:
        print(f"SKIP (no date): {title}  id={page.get('id')}")
        return None

    if is_datetime:
        start_ical = iso_to_ical_dt(start_iso, to_utc=True)
        end_ical   = iso_to_ical_dt(end_iso,   to_utc=True) if end_iso else start_ical
        dt_start = f"DTSTART:{start_ical}"
        dt_end   = f"DTEND:{end_ical}"
    else:
        start_ical = iso_to_ical_dt(start_iso, to_utc=False)
        if end_iso:
            end_ical = iso_to_ical_dt(end_iso, to_utc=False)
        else:
            y, m, d = start_ical[:4], start_ical[4:6], start_ical[6:8]
            plus1 = (datetime(int(y), int(m), int(d)) + timedelta(days=1)).strftime("%Y%m%d")
            end_ical = plus1
        dt_start = f"DTSTART;VALUE=DATE:{start_ical}"
        dt_end   = f"DTEND;VALUE=DATE:{end_ical}"

    description = rich_text_to_plain(desc_prop)
    location = rich_text_to_plain(loc_prop)

    lines = []
    lines.append("BEGIN:VEVENT")
    lines.append(f"UID:{page['id']}-notion@ics")
    now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines.append(f"DTSTAMP:{now_utc}")
    lines.append(dt_start)
    lines.append(dt_end)
    lines.append(f"SUMMARY:{ics_escape(title)}")
    if description:
        lines.append(f"DESCRIPTION:{ics_escape(description)}")
    if location:
        lines.append(f"LOCATION:{ics_escape(location)}")
    if page.get("url"):
        lines.append(f"URL:{ics_escape(page['url'])}")
    lines.append(f"X-NOTION-PAGE-ID:{page['id']}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def generate_ics(limit: Optional[int] = None) -> str:
    head = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Notion ICS Bridge//EN",
        f"X-WR-CALNAME:{ics_escape(FEED_NAME)}",
        f"X-WR-TIMEZONE:{ics_escape(FEED_TZ)}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]

    events = []
    for page in iter_notion_pages(NOTION_DB_ID):
        ve = notion_page_to_vevent(page)
        if ve:
            events.append(ve)

    if limit is not None:
        events = events[:limit]

    tail = ["END:VCALENDAR"]
    return "\r\n".join(head + events + tail) + "\r\n"



@app.route("/calendar-lite.ics")
def calendar_feed_lite():
    ics_data = cached_ics(limit=50)
    resp = make_response(ics_data, 200)
    resp.headers["Content-Type"] = "text/calendar; charset=utf-8"
    resp.headers["Content-Disposition"] = 'inline; filename="notion_flashcards_lite.ics"'
    resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route("/calendar.ics", methods=["GET", "HEAD"])
def calendar_feed():
    if request.method == "HEAD":
        resp = make_response("", 200)
    else:
        ics_text = cached_ics(limit=None)  # TÜM kartlar, cache'lenmiş
        raw = ics_text.encode("utf-8")

        # Büyük feed’lerde iOS’a nefes aldırmak için gzip iyi sonuç veriyor
        accept_enc = (request.headers.get("Accept-Encoding") or "").lower()
        if "gzip" in accept_enc and len(raw) >= 8192:
            import gzip
            from io import BytesIO
            buf = BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as f:
                f.write(raw)
            body = buf.getvalue()
            resp = make_response(body, 200)
            resp.headers["Content-Encoding"] = "gzip"
        else:
            resp = make_response(raw, 200)

    resp.headers["Content-Type"] = "text/calendar; charset=utf-8"
    resp.headers["Content-Disposition"] = "inline; filename=notion_flashcards.ics"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp




if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
