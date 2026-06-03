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

mysql_conn = None
neo4j_driver = None


# ── Kết nối database ────────────────────────────────────────────────────
def connect_mysql(max_retries=5, delay=3):
    for attempt in range(max_retries):
        try:
            conn = mysql.connector.connect(
                host=os.getenv("MYSQL_HOST"),
                port=int(os.getenv("MYSQL_PORT")),
                user=os.getenv("MYSQL_USER"),
                password=os.getenv("MYSQL_PASSWORD"),
                database=os.getenv("MYSQL_DB"),
                connection_timeout=5,
            )
            c = conn.cursor()
            c.execute("SELECT 1")
            c.fetchall()
            c.close()
            return conn
        except mysql.connector.Error as e:
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                raise e


def connect_neo4j(max_retries=5, delay=3):
    last_error = None
    for attempt in range(max_retries):
        try:
            driver = GraphDatabase.driver(
                os.getenv("NEO4J_URI"),
                auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
                max_connection_lifetime=3600,
            )
            with driver.session() as s:
                s.run("RETURN 1")
            return driver
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(delay)
    raise last_error


def init_connections():
    """Khởi tạo global connections. Gọi trước khi chạy benchmark."""
    global mysql_conn, neo4j_driver
    if mysql_conn is None:
        mysql_conn = connect_mysql()
    if neo4j_driver is None:
        neo4j_driver = connect_neo4j()


def close_connections():
    """Đóng global connections."""
    global mysql_conn, neo4j_driver
    if mysql_conn is not None:
        mysql_conn.close()
        mysql_conn = None
    if neo4j_driver is not None:
        neo4j_driver.close()
        neo4j_driver = None


def _retry_neo4j(max_attempts=10, base_delay=2):
    """Tạo driver mới, retry đến khi Neo4j sẵn sàng nhận kết nối."""
    for attempt in range(max_attempts):
        try:
            driver = GraphDatabase.driver(
                os.getenv("NEO4J_URI"),
                auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
                max_connection_lifetime=3600,
            )
            with driver.session() as s:
                s.run("RETURN 1")
            return driver
        except Exception:
            if attempt < max_attempts - 1:
                time.sleep(base_delay)
            else:
                raise


def _retry_mysql(max_attempts=10, base_delay=2):
    """Tạo connection mới, retry đến khi MySQL sẵn sàng nhận kết nối."""
    for attempt in range(max_attempts):
        try:
            conn = mysql.connector.connect(
                host=os.getenv("MYSQL_HOST"),
                port=int(os.getenv("MYSQL_PORT")),
                user=os.getenv("MYSQL_USER"),
                password=os.getenv("MYSQL_PASSWORD"),
                database=os.getenv("MYSQL_DB"),
                connection_timeout=5,
            )
            c = conn.cursor()
            c.execute("SELECT 1")
            c.fetchall()
            c.close()
            return conn
        except Exception:
            if attempt < max_attempts - 1:
                time.sleep(base_delay)
            else:
                raise


def ensure_mysql():
    """Đảm bảo MySQL connection còn sống. Tự reconnect nếu cần."""
    global mysql_conn
    if mysql_conn is None:
        mysql_conn = _retry_mysql()
        return
    try:
        c = mysql_conn.cursor(buffered=True)
        c.execute("SELECT 1")
        c.fetchall()
        c.close()
    except Exception:
        mysql_conn = _retry_mysql()


def ensure_neo4j():
    """Đảm bảo Neo4j connection còn sống. Tự reconnect nếu cần."""
    global neo4j_driver
    if neo4j_driver is None:
        neo4j_driver = _retry_neo4j()
        return
    try:
        with neo4j_driver.session() as s:
            s.run("RETURN 1")
    except Exception:
        neo4j_driver = _retry_neo4j()


# ── CHIẾN LƯỢC 1: Graph-first ──────────────────────────────────────────
def strategy_graph_first():
    """Graph-first: Neo4j → MySQL."""
    steps = {}
    t_total = time.perf_counter()

    # Bước 1: Neo4j tìm actors liên kết Kevin Bacon
    t0 = time.perf_counter()
    ensure_neo4j()
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
    ensure_neo4j()
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
    ensure_mysql()
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
    """SQL-first: MySQL → Neo4j."""
    steps = {}
    t_total = time.perf_counter()

    # Bước 1: MySQL lấy movie_ids có revenue > $100M
    t0 = time.perf_counter()
    ensure_mysql()
    cursor = mysql_conn.cursor(buffered=True)
    try:
        cursor.execute("SELECT movie_id FROM movies WHERE revenue > %s",
                       (REVENUE_THRESHOLD,))
        rich_movie_ids = [r[0] for r in cursor.fetchall()]
    finally:
        cursor.close()
    steps["mysql_filter_revenue"] = round((time.perf_counter() - t0) * 1000, 2)

    # Bước 2: Neo4j tìm actors đóng cùng Kevin Bacon trong những phim đó
    t0 = time.perf_counter()
    ensure_neo4j()
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


# ── CHẠY BENCHMARK (chỉ khi chạy trực tiếp) ───────────────────────────
if __name__ == "__main__":
    print("    Đang kết nối databases...")
    init_connections()

    print("=" * 60)
    print("   BENCHMARK: Multi-Model Join — Movies & Box Office")
    print("=" * 60)

    N = 5
    gf_totals, sf_totals = [], []
    gf_steps_all, sf_steps_all = [], []

    print(f"\nChạy mỗi chiến lược {N} lần...\n")

    for i in range(N):
        movies_gf, actors_gf, gf_steps, gf_total = strategy_graph_first()
        gf_totals.append(gf_total)
        gf_steps_all.append(gf_steps)

        results_sf, movie_ids_sf, sf_steps, sf_total = strategy_sql_first()
        sf_totals.append(sf_total)
        sf_steps_all.append(sf_steps)

        print(f"  Lần {i+1}: Graph-first={gf_total}ms | SQL-first={sf_total}ms")

    # Kết quả
    print("\n" + "=" * 60)
    print("   KẾT QUẢ BENCHMARK")
    print("=" * 60)

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
    print(tabulate(table,
                   headers=["Lần chạy", "Graph-first (ms)", "SQL-first (ms)"],
                   tablefmt="grid"))

    print("\n[2] Chi tiết bước thực thi (lần cuối):")
    gf_last = gf_steps_all[-1]
    sf_last = sf_steps_all[-1]
    detail = [
        ["Graph-first", "Neo4j: tìm actors",    gf_last["neo4j_find_actors"]],
        ["Graph-first", "Neo4j: tìm movies",    gf_last["neo4j_find_movies"]],
        ["Graph-first", "MySQL: lọc revenue",    gf_last["mysql_filter_revenue"]],
        ["SQL-first",   "MySQL: lọc revenue",    sf_last["mysql_filter_revenue"]],
        ["SQL-first",   "Neo4j: tìm actors",    sf_last["neo4j_find_actors"]],
    ]
    print(tabulate(detail,
                   headers=["Chiến lược", "Bước", "Thời gian (ms)"],
                   tablefmt="grid"))

    print(f"\n[3] Kết quả query:")
    print(f"    Actors liên kết Kevin Bacon : {len(actors_gf)}")
    print(f"    Phim doanh thu > $100M      : {len(movies_gf)}")
    print(f"    SQL-first kết quả           : {len(results_sf)}")

    print(f"\n[4] Danh sách phim tìm được (Graph-first):")
    for row in movies_gf:
        print(f"    - {row[1]} : ${row[2]:,}")

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

    os.makedirs("results", exist_ok=True)
    with open("results/benchmark_results.txt", "w", encoding="utf-8") as f:
        f.write(f"Graph-first avg: {avg_gf}ms\n")
        f.write(f"SQL-first avg:   {avg_sf}ms\n")
        f.write(f"Winner: {winner}\n")
        f.write(f"Runs: {gf_totals}\n")
        f.write(f"Runs: {sf_totals}\n")

    print("\nKết quả đã lưu vào results/benchmark_results.txt")

    close_connections()
