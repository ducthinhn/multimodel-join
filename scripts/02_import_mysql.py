import json
import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"),
    port=int(os.getenv("MYSQL_PORT")),
    user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"),
    database=os.getenv("MYSQL_DB")
)
cursor = conn.cursor()

print("=== Import dữ liệu vào MySQL ===")

# Đọc dữ liệu
with open("data/movies.json", encoding="utf-8") as f:
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