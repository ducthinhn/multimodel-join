import requests
import json
import time
import os
import sys
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("TMDB_API_KEY")
BASE = "https://api.themoviedb.org/3"
KEVIN_ID = 4724  # ID cố định của Kevin Bacon trên TMDB

# Đường dẫn tuyệt đối cho thư mục data
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# ── Kiểm tra API key trước khi bắt đầu ─────────────────────────────────────
if not API_KEY:
    print("❌ LỖI: TMDB_API_KEY không được tìm thấy trong file .env")
    print("   Hãy tạo file .env với nội dung: TMDB_API_KEY=your_key_here")
    print("   Lấy API key tại: https://www.themoviedb.org/settings/api")
    sys.exit(1)


def tmdb_get(endpoint, params={}):
    params["api_key"] = API_KEY
    try:
        r = requests.get(f"{BASE}/{endpoint}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        print(f"    ⚠️ Timeout khi gọi {endpoint} — đợi và thử lại...")
        time.sleep(2)
        r = requests.get(f"{BASE}/{endpoint}", params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"    ⚠️ Lỗi mạng khi gọi {endpoint}: {e}")
        raise

def get_movie_cast(movie_id):
    data = tmdb_get(f"movie/{movie_id}/credits")
    return data.get("cast", [])[:10]  # top 10 diễn viên

print("=== Thu thập dữ liệu từ TMDB ===")

# Bước 1: Lấy phim của Kevin Bacon
print("\n[1] Lấy phim của Kevin Bacon...")
kevin_data = tmdb_get(f"person/{KEVIN_ID}/movie_credits")
kevin_movies = kevin_data.get("cast", [])[:40]  # lấy 40 phim
print(f"    Tìm thấy {len(kevin_movies)} phim")

actors = {}   # {actor_id: actor_name}
movies = {}   # {movie_id: {title, revenue, year}}
edges  = []   # [(actor_id, movie_id)]

# Bước 2: Lấy chi tiết từng phim
print("\n[2] Lấy chi tiết từng phim (revenue + cast)...")
for i, movie in enumerate(kevin_movies):
    mid = movie["id"]
    title = movie.get("title", "Unknown")
    print(f"    [{i+1}/{len(kevin_movies)}] {title}")

    # Lấy revenue
    detail = tmdb_get(f"movie/{mid}")
    revenue = detail.get("revenue", 0)
    year = detail.get("release_date", "0000")[:4]

    movies[mid] = {
        "title": title,
        "revenue": revenue,
        "year": int(year) if year.isdigit() else 0
    }

    # Lấy cast
    cast = get_movie_cast(mid)
    for actor in cast:
        actors[actor["id"]] = actor["name"]
        edges.append([actor["id"], mid])

    time.sleep(0.3)  # tránh rate limit

# Bước 3: Lấy thêm co-actors của Kevin (mở rộng graph)
print("\n[3] Mở rộng graph - lấy phim của co-actors...")
kevin_actor_ids = set(e[0] for e in edges)
extra_count = 0

for actor_id in list(kevin_actor_ids)[:15]:  # lấy 15 co-actor đầu
    actor_movies = tmdb_get(f"person/{actor_id}/movie_credits").get("cast", [])[:5]
    for movie in actor_movies:
        mid = movie["id"]
        if mid not in movies:
            detail = tmdb_get(f"movie/{mid}")
            revenue = detail.get("revenue", 0)
            year = detail.get("release_date", "0000")[:4]
            movies[mid] = {
                "title": movie.get("title", "Unknown"),
                "revenue": revenue,
                "year": int(year) if year.isdigit() else 0
            }
            extra_count += 1
        cast = get_movie_cast(mid)
        for actor in cast:
            actors[actor["id"]] = actor["name"]
            edges.append([actor["id"], mid])
        time.sleep(0.25)

print(f"    Thêm {extra_count} phim mới")

# Bước 4: Lưu ra file JSON
print("\n[4] Lưu dữ liệu...")
os.makedirs(DATA_DIR, exist_ok=True)

with open(os.path.join(DATA_DIR, "movies.json"), "w", encoding="utf-8") as f:
    json.dump(movies, f, ensure_ascii=False, indent=2)

with open(os.path.join(DATA_DIR, "actors.json"), "w", encoding="utf-8") as f:
    json.dump(actors, f, ensure_ascii=False, indent=2)

with open(os.path.join(DATA_DIR, "edges.json"), "w", encoding="utf-8") as f:
    json.dump(edges, f)

print(f"""
=== Hoàn thành ===
  Movies : {len(movies)}
  Actors : {len(actors)}
  Edges  : {len(edges)}
  Files  : data/movies.json, data/actors.json, data/edges.json
""")