import os
import sys
import time
import mysql.connector
from neo4j import GraphDatabase
from dotenv import load_dotenv
from tabulate import tabulate

load_dotenv()

KEVIN_ID = 4724
REVENUE_THRESHOLD = 100_000_000

# ── Kết nối database với retry khởi tạo ────────────────────────────────
def connect_mysql(max_retries=5, delay=3):
    for attempt in range(max_retries):
        try:
            conn = mysql.connector.connect(
                host=os.getenv("MYSQL_HOST"),
                port=int(os.getenv("MYSQL_PORT")),
                user=os.getenv("MYSQL_USER"),
                password=os.getenv("MYSQL_PASSWORD"),
                database=os.getenv("MYSQL_DB"),
                connection_timeout=10,
                pool_name="benchmark_pool",
                pool_size=3,
            )
            check_cursor = conn.cursor()  
            check_cursor.execute("SELECT 1")
            check_cursor.fetchall()       
            check_cursor.close()
            print("    Kết nối MySQL thành công")
            return conn
        except mysql.connector.Error as e:
            print(f"   Kết nối MySQL lần {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                print(" Không thể kết nối MySQL. Thoát.")
                sys.exit(1)

def connect_neo4j(max_retries=5, delay=3):
    for attempt in range(max_retries):
        try:
            driver = GraphDatabase.driver(
                os.getenv("NEO4J_URI"),
                auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
                max_connection_lifetime=3600,
            )
            with driver.session() as s:
                s.run("RETURN 1")
            print("    Kết nối Neo4j thành công")
            return driver
        except Exception as e:
            print(f"   Kết nối Neo4j lần {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                print(" Không thể kết nối Neo4j. Thoát.")
                sys.exit(1)

print("    Đang kết nối databases...")
mysql_conn = connect_mysql()
neo4j_driver = connect_neo4j()

# ── CHIẾN LƯỢC 1: Graph-first ──────────────────────────────────────────
def strategy_graph_first():
    steps = {}
    t_total = time.perf_counter()

    # Bước 1: Neo4j tìm actors liên kết Kevin Bacon
    t0 = time.perf_counter()
    with neo4j_driver.session() as s:
        result = s.run("""
            MATCH (kevin:Person {id: $kid})-[:ACTED_IN]->(:Movie)<-[:ACTED_IN]-(actor:Person)
            WHERE actor.id <> $kid
            RETURN DISTINCT actor.id AS actor_id, actor.name AS actor_name
        """, kid=KEVIN_ID)
        connected_actors = [(r["actor_id"], r["actor_name"]) for r in result]
    steps["neo4j_find_actors"] = round((time.perf_counter() - t0) * 1000, 2)

    # Bước 2: Neo4j lấy movie_ids của các actors đó
    t0 = time.perf_counter()
    actor_ids = [a[0] for a in connected_actors]
    with neo4j_driver.session() as s:
        result = s.run("""
            MATCH (p:Person)-[:ACTED_IN]->(mv:Movie)
            WHERE p.id IN $ids
            RETURN DISTINCT mv.id AS movie_id
        """, ids=actor_ids)
        movie_ids = [r["movie_id"] for r in result]
    steps["neo4j_find_movies"] = round((time.perf_counter() - t0) * 1000, 2)

    # Bước 3: MySQL lọc revenue > $100M
    t0 = time.perf_counter()
    cursor = mysql_conn.cursor(buffered=True)
    try:
        if movie_ids:
            fmt = ",".join(["%s"] * len(movie_ids))
            cursor.execute(f"""
                SELECT m.movie_id, m.title, m.revenue
                FROM movies m
                WHERE m.movie_id IN ({fmt}) AND m.revenue > %s
                ORDER BY m.revenue DESC
            """, (*movie_ids, REVENUE_THRESHOLD))
        else:
            cursor.execute("""
                SELECT m.movie_id, m.title, m.revenue
                FROM movies m
                WHERE m.revenue > %s
                ORDER BY m.revenue DESC
                LIMIT 0
            """, (REVENUE_THRESHOLD,))
        qualifying_movies = cursor.fetchall()
    finally:
        cursor.close()
    steps["mysql_filter_revenue"] = round((time.perf_counter() - t0) * 1000, 2)

    total = round((time.perf_counter() - t_total) * 1000, 2)
    return qualifying_movies, connected_actors, steps, total

# ── CHIẾN LƯỢC 2: SQL-first ────────────────────────────────────────────
def strategy_sql_first():
    steps = {}
    t_total = time.perf_counter()

    # Bước 1: MySQL lấy movie_ids có revenue > $100M
    t0 = time.perf_counter()
    cursor = mysql_conn.cursor(buffered=True)
    try:
        cursor.execute("SELECT movie_id FROM movies WHERE revenue > %s", (REVENUE_THRESHOLD,))
        rich_movie_ids = [r[0] for r in cursor.fetchall()]
    finally:
        cursor.close()
    steps["mysql_filter_revenue"] = round((time.perf_counter() - t0) * 1000, 2)

    # Bước 2: Neo4j tìm actors đóng cùng Kevin Bacon trong những phim đó
    t0 = time.perf_counter()
    with neo4j_driver.session() as s:
        result = s.run("""
            MATCH (kevin:Person {id: $kid})-[:ACTED_IN]->(mv:Movie)<-[:ACTED_IN]-(actor:Person)
            WHERE mv.id IN $mids AND actor.id <> $kid
            RETURN DISTINCT actor.id AS actor_id, actor.name AS actor_name,
                            mv.id AS movie_id, mv.title AS movie_title
        """, kid=KEVIN_ID, mids=rich_movie_ids)
        results = [(r["actor_name"], r["movie_title"]) for r in result]
    steps["neo4j_find_actors"] = round((time.perf_counter() - t0) * 1000, 2)

    total = round((time.perf_counter() - t_total) * 1000, 2)
    return results, rich_movie_ids, steps, total

# ── CHẠY BENCHMARK ─────────────────────────────────────────────────────
print("=" * 60)
print("   BENCHMARK: Multi-Model Join — Movies & Box Office")
print("=" * 60)

N = 5  # số lần chạy

gf_totals, sf_totals = [], []
gf_steps_all, sf_steps_all = [], []

print(f"\nChạy mỗi chiến lược {N} lần...\n")

for i in range(N):
    movies_gf, actors_gf, gf_steps, gf_total = strategy_graph_first()
    gf_totals.append(gf_total)
    gf_steps_all.append(gf_steps)

    movies_sf, movie_ids_sf, sf_steps, sf_total = strategy_sql_first()
    sf_totals.append(sf_total)
    sf_steps_all.append(sf_steps)

    print(f"  Lần {i+1}: Graph-first={gf_total}ms | SQL-first={sf_total}ms")

# ── KẾT QUẢ ────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("   KẾT QUẢ BENCHMARK")
print("=" * 60)

# Tổng thời gian
print("\n[1] Thời gian thực thi (ms):")
table = []
for i in range(N):
    table.append([f"Lần {i+1}", gf_totals[i], sf_totals[i]])
table.append(["TRUNG BÌNH (cả 5 lần)",
               round(sum(gf_totals)/N, 2),
               round(sum(sf_totals)/N, 2)])
table.append(["TRUNG BÌNH (bỏ lần 1)",
               round(sum(gf_totals[1:])/(N-1), 2),
               round(sum(sf_totals[1:])/(N-1), 2)])
print(tabulate(table, headers=["Lần chạy", "Graph-first (ms)", "SQL-first (ms)"],
               tablefmt="grid"))

# Chi tiết từng bước (lần cuối)
print("\n[2] Chi tiết bước thực thi (lần cuối):")
gf_last = gf_steps_all[-1]
sf_last = sf_steps_all[-1]
detail = [
    ["Graph-first", "Neo4j: tìm actors", gf_last["neo4j_find_actors"]],
    ["Graph-first", "Neo4j: tìm movies", gf_last["neo4j_find_movies"]],
    ["Graph-first", "MySQL: lọc revenue", gf_last["mysql_filter_revenue"]],
    ["SQL-first",   "MySQL: lọc revenue", sf_last["mysql_filter_revenue"]],
    ["SQL-first",   "Neo4j: tìm actors", sf_last["neo4j_find_actors"]],
]
print(tabulate(detail, headers=["Chiến lược", "Bước", "Thời gian (ms)"],
               tablefmt="grid"))

# Kết quả query
print(f"\n[3] Kết quả query:")
print(f"    Actors liên kết Kevin Bacon : {len(actors_gf)}")
print(f"    Phim doanh thu > $100M      : {len(movies_gf)}")

print(f"\n[4] Danh sách phim tìm được (Graph-first):")
for row in movies_gf:
    print(f"    - {row[1]} : ${row[2]:,}")

# Kết luận
avg_gf = round(sum(gf_totals)/N, 2)
avg_sf = round(sum(sf_totals)/N, 2)
winner = "SQL-first" if avg_sf < avg_gf else "Graph-first"
diff = round(abs(avg_gf - avg_sf), 2)

print("\n" + "=" * 60)
print("   KẾT LUẬN")
print("=" * 60)
print(f"  Graph-first trung bình : {avg_gf} ms")
print(f"  SQL-first   trung bình : {avg_sf} ms")
print(f"  Chiến lược nhanh hơn   : {winner} (nhanh hơn {diff}ms)")
print("=" * 60)

# Lưu kết quả
os.makedirs("results", exist_ok=True)
with open("results/benchmark_results.txt", "w", encoding="utf-8") as f:
    f.write(f"Graph-first avg: {avg_gf}ms\n")
    f.write(f"SQL-first avg:   {avg_sf}ms\n")
    f.write(f"Winner: {winner}\n")
    f.write(f"Runs: {gf_totals}\n")
    f.write(f"Runs: {sf_totals}\n")

print("\nKết quả đã lưu vào results/benchmark_results.txt")

mysql_conn.close()
neo4j_driver.close()