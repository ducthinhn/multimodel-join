import json
import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD"))
)

print("=== Import dữ liệu vào Neo4j ===")

# Đọc dữ liệu
with open("data/actors.json", encoding="utf-8") as f:
    actors = json.load(f)
with open("data/movies.json", encoding="utf-8") as f:
    movies = json.load(f)
with open("data/edges.json", encoding="utf-8") as f:
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