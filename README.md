# Multi-Model Join: Movies & Box Office

Đề tài cuối kì môn Cơ sở dữ liệu phân tán — N23DCCN127.

## Mục lục

1. [Mô tả](#mô-tả)
2. [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
3. [Kết quả Benchmark](#kết-quả-benchmark)
4. [Dataset](#dataset)
5. [Cấu trúc project](#cấu-trúc-project)
6. [Cách chạy](#cách-chạy)
7. [Output mẫu](#output-mẫu)
8. [Tech Stack](#tech-stack)
9. [Phân tích](#phân-tích)
10. [Fault Tolerance Test Suite](#fault-tolerance-test-suite)
11. [Troubleshooting](#troubleshooting)
12. [Thông tin sinh viên](#thông-tin-sinh-viên)

---

## Mô tả

Thực hiện cross-model query: **"Tìm tất cả actors liên kết Kevin Bacon xuất hiện trong phim có doanh thu > $100M"**

Sử dụng 2 database khác loại:

- **Neo4j** (Graph DB) — lưu quan hệ diễn viên/phim (`Cast_Relationships`)
- **MySQL** (Relational DB) — lưu doanh thu phim (`Box_Office_Revenue`)

So sánh 2 chiến lược join:

- **Graph-first**: Neo4j → MySQL — duyệt graph tìm actors trước, lọc doanh thu sau
- **SQL-first**: MySQL → Neo4j — lọc doanh thu trước, tìm actors sau

---

## Yêu cầu hệ thống

| Yêu cầu | Phiên bản tối thiểu |
|---|---|
| Docker | Docker Desktop 4.x / Docker Engine + Compose v2 |
| Python | 3.10+ |
| RAM | 4GB trống (cho 2 containers) |
| Internet | Cần internet để pull Docker images và gọi TMDB API |

---

## Kết quả Benchmark

| Chiến lược | TB cả 5 lần (ms) | TB bỏ lần 1 (ms) | Ghi chú |
|---|---|---|---|
| Graph-first | 214.41 | 47.59 | Lần 1 cold start ~881ms |
| SQL-first | 28.12 | 16.50 | Ổn định hơn |
| **Winner** | **SQL-first** | **SQL-first** | Nhanh hơn ~2.9 lần (warm runs) |

---

## Dataset

- **Nguồn:** [TMDB API](https://www.themoviedb.org/)
- **102 phim** · **816 diễn viên** · **991 quan hệ ACTED_IN**
- **Tâm graph:** Kevin Bacon (TMDB ID: 4724)
- **Phim doanh thu > $100M:** 27 phim
- **Actors liên kết Kevin Bacon:** 326 actors

---

## Cấu trúc project

```
multimodel-join/
├── docker-compose.yml   # Neo4j + MySQL containers
├── .env                 # API keys, passwords
├── requirements.txt     # Python dependencies
├── data/
│   ├── movies.json      # Dữ liệu phim từ TMDB
│   ├── actors.json      # Dữ liệu diễn viên
│   └── edges.json       # Quan hệ ACTED_IN
├── scripts/
│   ├── 01_collect_data.py    # Thu thập dữ liệu từ TMDB API
│   ├── 02_import_mysql.py    # Import doanh thu vào MySQL
│   ├── 03_import_neo4j.py    # Import graph vào Neo4j
│   ├── benchmark.py           # Benchmark 2 chiến lược join
│   └── 04_test_failures.py   # Fault tolerance — 13 kịch bản lỗi
├── sql/
│   └── schema.sql      # Schema MySQL (bảng Box_Office_Revenue)
└── results/
    ├── benchmark_results.txt       # Kết quả benchmark
    └── fault_tolerance_results.txt # Kết quả fault tolerance
```

---

## Cách chạy

### 1. Khởi động database

```bash
docker compose up -d
```

Đợi 15-20 giây để containers khởi động hoàn toàn.

### 2. Cài đặt Python dependencies

```bash
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # Linux/macOS

pip install -r requirements.txt
```

### 3. Cấu hình `.env`

Tạo file `.env` trong thư mục gốc:

```env
# TMDB API — lấy key tại https://www.themoviedb.org/settings/api
TMDB_API_KEY=your_key_here

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password

# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3307
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DB=boxoffice
```

### 4. Chạy lần lượt

```bash
# Thu thập dữ liệu từ TMDB
python scripts/01_collect_data.py

# Import vào MySQL
python scripts/02_import_mysql.py

# Import vào Neo4j
python scripts/03_import_neo4j.py

# Benchmark 2 chiến lược
python scripts/benchmark.py

# Fault tolerance tests
python scripts/04_test_failures.py
```

---

## Output mẫu

### Benchmark output

```
================================================================
   BENCHMARK: Multi-Model Join — Movies & Box Office
================================================================
Chạy mỗi chiến lược 5 lần...

  Lần 1: Graph-first=881.68ms | SQL-first=74.59ms
  Lần 2: Graph-first=48.83ms | SQL-first=16.46ms
  Lần 3: Graph-first=47.87ms | SQL-first=18.30ms
  Lần 4: Graph-first=47.35ms | SQL-first=15.17ms
  Lần 5: Graph-first=46.30ms | SQL-first=16.07ms

================================================================
   KẾT LUẬN
================================================================
  Graph-first trung bình : 214.41 ms
  SQL-first   trung bình : 28.12 ms
  Chiến lược nhanh hơn   : SQL-first (nhanh hơn 186.29ms)
================================================================
```

### Fault tolerance output

```
================================================================
   FAULT TOLERANCE TEST SUITE
================================================================
  [1/13] T1: MySQL crash (hoàn toàn)...
  ✅ PASSED — Connection refused detected correctly

  [2/13] T2: MySQL crash (giữa Graph-first)...
  ✅ PASSED — Crashed at step 3 as expected

  ...

================================================================
   TỔNG KẾT
================================================================
   Tests passed : 6/6
   Results saved : results/fault_tolerance_results.txt
================================================================
```

---

## Tech Stack

| Công nghệ | Phiên bản | Vai trò |
|---|---|---|
| Neo4j | 5.x | Graph Database — Cast_Relationships |
| MySQL | 8.0 | Relational Database — Box_Office_Revenue |
| Docker | 29.x | Container hóa databases |
| Python | 3.14 | Coordinator, benchmark, fault injection |
| neo4j-driver | 5.15 | Giao tiếp Neo4j qua Bolt |
| mysql-connector-python | 8.3 | Giao tiếp MySQL qua TCP |
| TMDB API | v3 | Nguồn dữ liệu phim và diễn viên |
| tabulate | 0.9 | Format bảng kết quả |
| python-dotenv | 1.0 | Quản lý biến môi trường |

---

## Phân tích

### Tại sao SQL-first nhanh hơn?

**Graph-first (Neo4j → MySQL):**

1. Neo4j tìm 326 actors liên kết Kevin Bacon
2. Neo4j lấy tất cả movies của 326 actors đó
3. Truyền hàng trăm movie IDs sang MySQL
4. MySQL lọc revenue > $100M

→ Vấn đề: lượng dữ liệu trung gian lớn → network I/O chiếm nhiều thời gian

**SQL-first (MySQL → Neo4j):**

1. MySQL lọc 27 phim doanh thu > $100M
2. Truyền 27 movie IDs sang Neo4j
3. Neo4j tìm actors liên kết Kevin Bacon trong 27 phim đó

→ Ưu điểm: semi-join reduction — filter SQL nghiêm ngặt trước → thu hẹp tập dữ liệu → network I/O giảm đáng kể

### Khi nào dùng Graph-first?

- Khi filter SQL rất rộng (hầu hết phim đều qualify)
- Khi cần duyệt graph nhiều bậc (Kevin Bacon Number > 2)
- Khi dữ liệu graph thưa (ít edges)

### Cold Start Analysis

Lần chạy đầu tiên chậm hơn đáng kể (881ms vs 47ms) do:

- **JVM Warm-up**: Neo4j chạy trên JVM — lần đầu phải load và compile bytecode
- **Disk I/O**: Lần đầu đọc dữ liệu từ ổ cứng, các lần sau đọc từ RAM cache
- **Connection Pool**: Driver phải thiết lập TCP/Bolt connection mới ở lần đầu

→ Do đó benchmark báo cáo 2 con số: trung bình cả 5 lần và bỏ lần 1 (warm runs).

---

## Fault Tolerance Test Suite

### Tổng quan

Hệ thống được kiểm thử với **13 kịch bản lỗi** phân tán theo lý thuyết
Özsu & Valduriez (Chapter 8: Fault Tolerance and Recovery).

### Các kịch bản

| ID | Kịch bản | Mô tả | Mong đợi |
|---|---|---|---|
| T1 | MySQL crash hoàn toàn | MySQL tắt trước Graph-first | Phát hiện lỗi kết nối rõ ràng |
| T2 | MySQL crash giữa Graph-first | Neo4j bước 1+2 xong → MySQL bước 3 fail | Partial result từ Neo4j được ghi nhận |
| T3 | Neo4j crash hoàn toàn | Neo4j tắt trước SQL-first | Phát hiện lỗi Bolt connection |
| T4 | Neo4j crash giữa SQL-first | MySQL bước 1 xong → Neo4j bước 2 fail | Partial result từ MySQL được ghi nhận |
| T5 | Network partition | Cắt mạng giữa 2 container (dbnet disconnect) | Timeout hợp lý, không treo vô hạn |
| T6 | MySQL chậm/timeout | MySQL không phản hồi | Retry với exponential backoff |
| T7 | Neo4j chậm/timeout | Neo4j transaction treo | Retry hoặc báo lỗi rõ ràng |
| T8 | Total outage | Cả 2 node cùng crash đồng thời | Graceful degradation, recovery riêng từng node |
| T9 | Coordinator crash | Python process bị kill trong lúc chạy | Database container vẫn chạy, resume được |
| T10 | Data inconsistency | MySQL crash không graceful | Phát hiện result count bất thường |
| T11 | Retry thành công | MySQL crash ngắn + retry thành công | Coordinator tự retry, truy vấn hoàn tất |
| T12 | Partial failure Graph-first | MySQL die ở bước 3, Neo4j steps 1+2 hoàn thành | Partial result được bảo toàn |
| T13 | Partial failure SQL-first | Neo4j die ở bước 2, MySQL step 1 hoàn thành | Partial result được bảo toàn |

### Cách chạy

```bash
# Chạy tất cả 13 kịch bản
python scripts/04_test_failures.py

# Chạy 1 test cụ thể
python scripts/04_test_failures.py --test T5

# Chạy nhiều test cụ thể
python scripts/04_test_failures.py --test T1,T3,T5

# Bỏ qua test cụ thể
python scripts/04_test_failures.py --skip T9
```

### Đánh giá kết quả

- **✅ PASSED**: Hệ thống xử lý lỗi đúng như mong đợi
- **❌ FAILED**: Hệ thống không xử lý đúng → cần cải thiện

Kết quả được lưu vào `results/fault_tolerance_results.txt`.

### Chiến lược Fault Tolerance

1. **Retry với Exponential Backoff**: Database timeout → retry tối đa `MAX_RETRIES=3` lần, độ trễ 1s → 2s → 4s
2. **Partial Result**: Một node fail giữa chừng → kết quả từ các bước đã hoàn thành vẫn được ghi nhận
3. **Graceful Degradation**: Hệ thống báo lỗi rõ ràng thay vì crash không kiểm soát
4. **Recovery Tracking**: Thời gian phục hồi (recovery time) được ghi nhận cho từng node

---

## Troubleshooting

**`Connection refused` khi chạy script**
→ Đợi 15-20 giây sau `docker compose up -d` để container khởi động hoàn toàn.
→ Hoặc chạy `docker ps` để kiểm tra trạng thái containers.

**`Invalid API key` từ TMDB**
→ Kiểm tra file `.env`, đảm bảo `TMDB_API_KEY=your_actual_key` (không có dấu ngoặc kép, không có khoảng trắng thừa).

**`Too many connections` từ MySQL**
→ Khởi động lại: `docker restart mysql_db`

**Neo4j không trả lời sau khi restart**
→ Kiểm tra: `docker logs neo4j_db`
→ Đợi thêm 30 giây sau restart.

**Docker container không khởi động được**
→ Kiểm tra port đã bị chiếm chưa: `docker ps`
→ Kiểm tra logs: `docker compose logs`

---

## Thông tin sinh viên

| Thông tin | Chi tiết |
|---|---|
| Họ và tên | Nguyễn Đức Thịnh |
| MSSV | N23DCCN127 |
| Lớp | D23CQCN02-N |
| Giáo viên hướng dẫn | Thầy Lê Hà Thanh |
| Môn học | Cơ sở dữ liệu phân tán |
| Mã đề tài | #133 — Multi-Model Join (Category 14) |
| Năm học | 2025–2026 |

---

*Lý thuyết tham khảo: T. Özsu and P. Valduriez, Principles of Distributed Database Systems, 4th ed., Springer, 2020.*
