import json
import os
import sys
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

# Đường dẫn tuyệt đối
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# ── Kết nối với retry ────────────────────────────────────────────────────
def connect_mysql(max_retries=5, delay=3):
    """Kết nối MySQL với retry nếu chưa sẵn sàng."""
    for attempt in range(max_retries):
        try:
            conn = mysql.connector.connect(
                host=os.getenv("MYSQL_HOST"),
                port=int(os.getenv("MYSQL_PORT")),
                user=os.getenv("MYSQL_USER"),
                password=os.getenv("MYSQL_PASSWORD"),
                database=os.getenv("MYSQL_DB"),
                connection_timeout=10,
            )
            # --- SỬA ĐOẠN VERIFY NÀY ---
            check_cursor = conn.cursor()
            check_cursor.execute("SELECT 1")
            check_cursor.fetchall()  
            check_cursor.close()     
            # ---------------------------
            
            print("   Kết nối MySQL thành công")
            return conn
        except mysql.connector.Error as e:
            print(f"   Lần {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                print(f"    Đợi {delay}s trước khi thử lại...")
                time.sleep(delay)
            else:
                print(" Không thể kết nối MySQL sau nhiều lần thử. Thoát.")
                sys.exit(1)

import time
conn = connect_mysql()
cursor = conn.cursor()

print("=== Import dữ liệu vào MySQL ===")

# Đọc dữ liệu
data_file = os.path.join(DATA_DIR, "movies.json")
if not os.path.exists(data_file):
    print(f" Không tìm thấy file dữ liệu: {data_file}")
    print(f"   Hãy chạy scripts/01_collect_data.py trước.")
    sys.exit(1)

with open(data_file, encoding="utf-8") as f:
    movies = json.load(f)

# Insert từng phim
print(f"\n[1] Import {len(movies)} phim...")
success = 0
for mid, m in movies.items():
    try:
        cursor.execute("""
            INSERT INTO movies (movie_id, title, release_year, revenue)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                title=VALUES(title),
                release_year=VALUES(release_year),
                revenue=VALUES(revenue)
        """, (int(mid), m["title"], m["year"], m["revenue"]))
        success += 1
    except Exception as e:
        print(f"    Lỗi movie {mid}: {e}")

conn.commit()
print(f"    Import thành công: {success}/{len(movies)} phim")

# Kiểm tra phim có revenue > $100M
cursor.execute("SELECT COUNT(*) FROM movies WHERE revenue > 100000000")
count = cursor.fetchone()[0]
print(f"\n[2] Phim có doanh thu > $100M: {count} phim")

# Hiển thị top 5
cursor.execute("""
    SELECT title, revenue, release_year 
    FROM movies 
    WHERE revenue > 100000000 
    ORDER BY revenue DESC 
    LIMIT 5
""")
rows = cursor.fetchall()
print("\n    Top 5 phim doanh thu cao nhất:")
for row in rows:
    print(f"    - {row[0]} ({row[2]}): ${row[1]:,}")

cursor.close()
conn.close()

print("\n=== Hoàn thành import MySQL ===")