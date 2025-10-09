import os, json
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()
db_id = os.getenv("NOTION_DATABASE_ID")
token = os.getenv("NOTION_TOKEN")

# İstersen Notion versiyonunu sabitle:
notion = Client(auth=token)  # gerekirse: Client(auth=token, notion_version="2022-06-28")

print("DB:", db_id)
resp = notion.databases.query(database_id=db_id, page_size=5)
items = resp.get("results", [])
print("Toplanan ilk sayfa kayıt adedi:", len(items))

if not items:
    print("Hiç kayıt dönmedi. Büyük ihtimalle yanlış DB ID ya da Connection ekli değil.")
else:
    props = items[0]["properties"]
    print("İlk kaydın property adları:", list(props.keys()))
    # Tüm 'date' tipli property’leri ve değerlerini yaz:
    date_fields = {k: v for k, v in props.items() if v.get("type") == "date"}
    pretty = {k: date_fields[k]["date"] for k in date_fields}
    print("Date tipli alanlar ve değerleri:", json.dumps(pretty, ensure_ascii=False, indent=2))
