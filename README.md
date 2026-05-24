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
│   ├── 01_collect_data.py  # Thu thập dữ liệu TMDB
│   ├── 02_import_mysql.py  # Import vào MySQL
│   ├── 03_import_neo4j.py # Import vào Neo4j
│   ├── benchmark.py        # Benchmark 2 chiến lược (Graph-first vs SQL-first)
│   └── 04_test_failures.py # Fault tolerance test suite (13 kịch bản lỗi)
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
python scripts/benchmark.py
python scripts/04_test_failures.py  # fault tolerance tests
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

---

## Fault Tolerance Test Suite

### Tổng quan

Hệ thống Multi-Model Join được kiểm thử với **13 kịch bản lỗi** phân tán,
chứng minh khả năng chịu lỗi (fault tolerance) và phục hồi (recovery) trong
môi trường multi-database.

### Các kịch bản lỗi

| ID  | Kịch bản | Mô tả | Mong đợi |
|-----|----------|--------|-----------|
| T1  | MySQL crash trước Graph-first | MySQL bị tắt hoàn toàn trước khi Graph-first chạy | Phát hiện lỗi kết nối, báo lỗi rõ ràng |
| T2  | MySQL crash giữa Graph-first | Neo4j bước 1+2 xong → MySQL bước 3 fail | Partial result từ Neo4j được ghi nhận |
| T3  | Neo4j crash trước SQL-first | Neo4j bị tắt hoàn toàn trước khi SQL-first chạy | Phát hiện lỗi Bolt connection |
| T4  | Neo4j crash giữa SQL-first | MySQL bước 1 xong → Neo4j bước 2 fail | Partial result từ MySQL được ghi nhận |
| T5  | Network partition | Cắt mạng giữa 2 container (dbnet disconnect) | Timeout hợp lý, không treo vô hạn |
| T6  | MySQL chậm/timeout | MySQL không phản hồi (timeout) | Retry với exponential backoff |
| T7  | Neo4j chậm/timeout | Neo4j transaction bị treo | Retry hoặc báo lỗi rõ ràng |
| T8  | Total outage | Cả 2 node cùng crash đồng thời | Graceful degradation, recovery riêng từng node |
| T9  | Coordinator crash | Python process bị kill trong lúc chạy | Database container vẫn chạy, resume được |
| T10 | Data inconsistency | MySQL crash không graceful, dữ liệu bị rollback | Phát hiện result count bất thường, cảnh báo |
| T11 | Retry thành công | MySQL crash ngắn + retry thành công | Coordinator tự retry, truy vấn hoàn tất |
| T12 | Partial failure Graph-first | MySQL die ở bước 3/3, Neo4j steps 1+2 hoàn thành | Partial result được bảo toàn |
| T13 | Partial failure SQL-first | Neo4j die ở bước 2/2, MySQL step 1 hoàn thành | Partial result được bảo toàn |

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

- **✅ PASSED** : Hệ thống xử lý lỗi đúng như mong đợi
- **❌ FAILED** : Hệ thống không xử lý đúng → cần cải thiện

Kết quả được lưu vào `results/fault_tolerance_results.txt`.

### Các chiến lược fault tolerance

1. **Retry với Exponential Backoff**: Khi database timeout, hệ thống retry
   tối đa `MAX_RETRIES=3` lần với độ trễ tăng dần (1s → 2s → 4s)
2. **Partial Result**: Khi một node fail giữa chừng, kết quả từ các bước
   đã hoàn thành vẫn được ghi nhận và trả về
3. **Graceful Degradation**: Hệ thống báo lỗi rõ ràng thay vì crash
   không kiểm soát, cho phép coordinator quyết định hành động tiếp theo
4. **Recovery Tracking**: Thời gian phục hồi (recovery time) được ghi
   nhận cho từng node sau khi crash
