import json
import os
import sys
import time
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

# Đường dẫn tuyệt đối
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# ── Kết nối Neo4j với retry ───────────────────────────────────────────────
def connect_neo4j(max_retries=5, delay=3):
    """Kết nối Neo4j với retry nếu chưa sẵn sàng."""
    for attempt in range(max_retries):
        try:
            driver = GraphDatabase.driver(
                os.getenv("NEO4J_URI"),
                auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
                max_connection_lifetime=3600,
            )
            # Verify connection bằng ping
            with driver.session() as s:
                s.run("RETURN 1")
            print("    ✅ Kết nối Neo4j thành công")
            return driver
        except Exception as e:
            print(f"    ⚠️  Lần {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                print(f"    Đợi {delay}s trước khi thử lại...")
                time.sleep(delay)
            else:
                print("❌ Không thể kết nối Neo4j sau nhiều lần thử. Thoát.")
                sys.exit(1)

driver = connect_neo4j()

print("=== Import dữ liệu vào Neo4j ===")

# Đọc dữ liệu
data_files = {
    "actors": os.path.join(DATA_DIR, "actors.json"),
    "movies": os.path.join(DATA_DIR, "movies.json"),
    "edges":  os.path.join(DATA_DIR, "edges.json"),
}
for name, path in data_files.items():
    if not os.path.exists(path):
        print(f"❌ Không tìm thấy file: {path}")
        print(f"   Hãy chạy scripts/01_collect_data.py trước.")
        sys.exit(1)

with open(data_files["actors"], encoding="utf-8") as f:
    actors = json.load(f)
with open(data_files["movies"], encoding="utf-8") as f:
    movies = json.load(f)
with open(data_files["edges"], encoding="utf-8") as f:
    edges = json.load(f)

with driver.session() as s:

    # Xóa dữ liệu cũ
    print("\n[1] Xóa dữ liệu cũ...")
    s.run("MATCH (n) DETACH DELETE n")

    # Import actors
    print(f"\n[2] Import {len(actors)} actors...")
    actor_list = [{"id": int(k), "name": v} for k, v in actors.items()]
    s.run("""
        UNWIND $data AS a
        MERGE (p:Person {id: a.id})
        SET p.name = a.name
    """, data=actor_list)
    print(f"    Xong!")

    # Import movies
    print(f"\n[3] Import {len(movies)} movies...")
    movie_list = [{"id": int(k), "title": v["title"], "revenue": v["revenue"]} 
                  for k, v in movies.items()]
    s.run("""
        UNWIND $data AS m
        MERGE (mv:Movie {id: m.id})
        SET mv.title = m.title, mv.revenue = m.revenue
    """, data=movie_list)
    print(f"    Xong!")

    # Import relationships
    print(f"\n[4] Import {len(edges)} relationships (ACTED_IN)...")
    s.run("""
        UNWIND $data AS e
        MATCH (p:Person {id: e[0]})
        MATCH (mv:Movie {id: e[1]})
        MERGE (p)-[:ACTED_IN]->(mv)
    """, data=edges)
    print(f"    Xong!")

    # Kiểm tra
    print("\n[5] Kiểm tra dữ liệu trong Neo4j...")
    r = s.run("MATCH (p:Person) RETURN COUNT(p) AS cnt").single()
    print(f"    Persons : {r['cnt']}")
    r = s.run("MATCH (m:Movie) RETURN COUNT(m) AS cnt").single()
    print(f"    Movies  : {r['cnt']}")
    r = s.run("MATCH ()-[r:ACTED_IN]->() RETURN COUNT(r) AS cnt").single()
    print(f"    ACTED_IN: {r['cnt']}")

    # Kiểm tra Kevin Bacon
    r = s.run("""
        MATCH (p:Person {id: 4724})
        RETURN p.name AS name
    """).single()
    print(f"\n    Kevin Bacon tìm thấy: {r['name'] if r else 'KHÔNG TÌM THẤY!'}")

driver.close()
print("\n=== Hoàn thành import Neo4j ===")