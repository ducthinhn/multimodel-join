markdown# Multi-Model Join: Movies & Box Office

Đề tài cuối kì môn Cơ sở dữ liệu phân tán.

## Mô tả

Thực hiện cross-model query: **"Tìm tất cả actors liên kết Kevin Bacon xuất hiện trong phim có doanh thu > $100M"**

Sử dụng 2 database khác loại:

- **Neo4j** (Graph DB) — lưu quan hệ diễn viên/phim (`Cast_Relationships`)
- **MySQL** (Relational DB) — lưu doanh thu phim (`Box_Office_Revenue`)

So sánh 2 chiến lược join:

- **Graph-first**: Tìm actors trên Neo4j trước → lọc doanh thu trên MySQL sau
- **SQL-first**: Lọc doanh thu trên MySQL trước → tìm actors trên Neo4j sau

---

## Kết quả Benchmark

| Chiến lược | TB cả 5 lần | TB bỏ lần 1 | Ghi chú |
|---|---|---|---|
| Graph-first | 214.41 ms | 47.59 ms | Lần 1 cold start ~881ms |
| SQL-first | 28.12 ms | 16.50 ms | Ổn định hơn |
| **Winner** | **SQL-first** | **SQL-first** | Nhanh hơn ~2.9 lần (warm) |

---

## Dataset

- Nguồn: [TMDB API](https://www.themoviedb.org/)
- 102 phim · 816 diễn viên · 991 quan hệ ACTED_IN
- Tâm graph: Kevin Bacon (TMDB ID: 4724)
- Phim doanh thu > $100M: 29 phim
- Actors liên kết Kevin Bacon: 326 actors

---

## Cấu trúc project

multimodel-join/
├── docker-compose.yml # Neo4j + MySQL containers
├── .env # API keys, passwords
├── requirements.txt # Python dependencies
├── data/ # Raw data từ TMDB API
│ ├── movies.json
│ ├── actors.json
│ └── edges.json
├── scripts/
│ ├── 01_collect_data.py # Thu thập dữ liệu TMDB
│ ├── 02_import_mysql.py # Import vào MySQL
│ ├── 03_import_neo4j.py # Import vào Neo4j
│ └── 04_benchmark.py # Benchmark 2 chiến lược
├── sql/
│ └── schema.sql # Schema MySQL
└── results/
└── benchmark_results.txt

---

## Cách chạy

### 1. Khởi động database

```bash
docker compose up -d
```

### 2. Cài thư viện

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Điền API key vào `.env`

TMDB_API_KEY=your_key_here

### 4. Chạy lần lượt

```bash
python scripts/01_collect_data.py
python scripts/02_import_mysql.py
python scripts/03_import_neo4j.py
python scripts/04_benchmark.py
```

---

## Tech Stack

| Công nghệ   | Vai trò                       |
| ----------- | ----------------------------- |
| Neo4j 5.x   | Graph Database                |
| MySQL 8.0   | Relational Database           |
| Python 3.14 | Script xử lý + benchmark      |
| Docker      | Chạy database trong container |
| TMDB API    | Nguồn dữ liệu phim            |

---

## Phân tích

### Tại sao SQL-first nhanh hơn?

**Graph-first:**
Neo4j → 326 actors → lấy tất cả movies của họ → truyền nhiều ID → MySQL lọc
Vấn đề: lượng dữ liệu trung gian lớn (326 actors → hàng trăm movie IDs)

**SQL-first:**
MySQL → 29 phim > $100M → truyền 29 ID → Neo4j tìm actors
Ưu điểm: filter SQL nghiêm ngặt trước → thu hẹp tập dữ liệu → Neo4j xử lý ít hơn

### Khi nào dùng Graph-first?

- Khi filter SQL rất rộng (hầu hết phim đều qualify)
- Khi cần duyệt graph nhiều bậc (Kevin Bacon Number > 2)
- Khi dữ liệu graph thưa (ít edges)
