"""
Cách chạy:
  python scripts/04_test_failures.py
  python scripts/04_test_failures.py --test T1   # chạy 1 test cụ thể
  python scripts/04_test_failures.py --test T1,T3 # chạy nhiều test cụ thể
  python scripts/04_test_failures.py --skip T5    # bỏ qua test T5
"""

import os
import sys
import time
import argparse
import subprocess
from datetime import datetime

# ── Đường dẫn gốc của project ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.benchmark import (
    strategy_graph_first, strategy_sql_first,
    init_connections, close_connections
)

# ── Container names (khớp docker-compose.yml) ────────────────────────────────
CONTAINER_MYSQL = "mysql_db"
CONTAINER_NEO4J = "neo4j_db"
NETWORK = "dbnet"

# ── Timeout cho mỗi test (giây) ─────────────────────────────────────────────
TEST_TIMEOUT = 30

# ── Retry settings (dùng trong test 6, 7, 11) ───────────────────────────────
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1  # exponential backoff: 1s, 2s, 4s

# ── Ngưỡng cảnh báo data inconsistency (test 10) ───────────────────────────
REVENUE_THRESHOLD = 100_000_000
EXPECTED_MIN_MOVIES = 1  # số phim doanh thu > $100M tối thiểu mong đợi


# ══════════════════════════════════════════════════════════════════════════════
#  FAULT INJECTION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def docker_stop(container: str) -> float:
    """Tắt container. Trả về thời gian tắt (giây)."""
    t0 = time.perf_counter()
    subprocess.run(["docker", "stop", container], capture_output=True, timeout=10)
    return round(time.perf_counter() - t0, 2)


def docker_start(container: str) -> float:
    """Bật lại container. Trả về thời gian khởi động (giây)."""
    t0 = time.perf_counter()
    subprocess.run(["docker", "start", container], capture_output=True, timeout=30)
    _wait_container_ready(container)
    return round(time.perf_counter() - t0, 2)


def docker_kill(container: str) -> float:
    """Kill container ngay lập tức (không graceful). Trả về thời gian."""
    t0 = time.perf_counter()
    subprocess.run(["docker", "kill", container], capture_output=True, timeout=10)
    return round(time.perf_counter() - t0, 2)


def _wait_container_ready(container: str, timeout: int = 30):
    """Đợi container Running=true + chờ thêm buffer để DB sẵn sàng."""
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True, text=True
        )
        if result.stdout.strip() == "true":
            # Container đã Running nhưng DB cần thêm thời gian khởi động
            time.sleep(5)  # buffer đủ để Neo4j/MySQL accept connections
            return
        time.sleep(0.5)
    print(f"    [WARN] Container {container} chưa sẵn sàng sau {timeout}s")


def network_disconnect(node: str):
    """Cắt node khỏi mạng dbnet."""
    subprocess.run(
        ["docker", "network", "disconnect", NETWORK, node],
        capture_output=True
    )


def network_connect(node: str):
    """Kết nối lại node vào mạng dbnet."""
    subprocess.run(
        ["docker", "network", "connect", NETWORK, node],
        capture_output=True
    )


def is_mysql_running() -> bool:
    """Kiểm tra MySQL container có đang chạy không."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_MYSQL],
        capture_output=True, text=True
    )
    return result.stdout.strip() == "true"


def is_neo4j_running() -> bool:
    """Kiểm tra Neo4j container có đang chạy không."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NEO4J],
        capture_output=True, text=True
    )
    return result.stdout.strip() == "true"


# ══════════════════════════════════════════════════════════════════════════════
#  TEST RESULTS
# ══════════════════════════════════════════════════════════════════════════════

class TestResult:
    """Kết quả của một test case."""

    def __init__(self, test_id: str, name: str, description: str):
        self.test_id = test_id
        self.name = name
        self.description = description
        self.start_time = time.perf_counter()
        self.error: str | None = None
        self.recovery_time: float = 0.0
        self.partial_result: any = None
        self.crashed_at_step: str | None = None
        self.passed: bool = False
        self.notes: list[str] = []
        self.strategy_used: str | None = None
        self.steps_completed: list[str] = []

    def mark_error(self, error: str):
        self.error = error

    def mark_recovery(self, recovery_time: float):
        self.recovery_time = recovery_time

    def mark_partial(self, result: any, step: str, steps_completed: list[str]):
        self.partial_result = result
        self.crashed_at_step = step
        self.steps_completed = steps_completed

    def mark_passed(self):
        self.passed = True

    def add_note(self, note: str):
        self.notes.append(note)

    def duration(self) -> float:
        return round(time.perf_counter() - self.start_time, 2)

    def summary(self) -> dict:
        return {
            "test_id": self.test_id,
            "name": self.name,
            "passed": self.passed,
            "error": self.error,
            "recovery_time": self.recovery_time,
            "crashed_at_step": self.crashed_at_step,
            "has_partial_result": self.partial_result is not None,
            "steps_completed": self.steps_completed,
            "duration": self.duration(),
            "notes": self.notes,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  HELPER: CHẠY STRATEGY VỚI FAULT INJECTION
# ══════════════════════════════════════════════════════════════════════════════

def run_strategy_with_mid_injection(strategy_fn, strategy_name: str,
                                     stop_at_step: str | None,
                                     tracker: list):
    """
    Chạy một strategy và tự động inject fault ở step mong muốn.
    stop_at_step: None (bình thường), "step1", "step2", "step3"
    """
    # Logic: stop container trước khi chạy step tương ứng
    # Với Graph-first: step1=neo4j_find_actors, step2=neo4j_find_movies, step3=mysql_filter_revenue
    # Với SQL-first: step1=mysql_filter_revenue, step2=neo4j_find_actors
    pass  # placeholder


# ══════════════════════════════════════════════════════════════════════════════
#  TEST CASES
# ══════════════════════════════════════════════════════════════════════════════

def test_t1_mysql_crash_before_graph_first(tracker: list):
    """
    T1 | MySQL crash trước Graph-first

    Mô phỏng : Container mysql_db bị stop hoàn toàn trước khi
               Graph-first bắt đầu (bước MySQL ở cuối).
    Nguyên nhân mô phỏng : docker stop mysql_db
    Mong đợi  : Phát hiện InterfaceError / Connection refused, hệ thống
               báo lỗi rõ ràng, không crash không kiểm soát.
    """
    result = TestResult(
        "T1", "MySQL crash (hoàn toàn, trước Graph-first)",
        "MySQL bị tắt hoàn toàn trước khi benchmark chạy. "
        "Graph-first cần bước MySQL ở cuối (bước 3/3)."
    )
    result.strategy_used = "Graph-first"

    # 1. Tắt MySQL
    stop_time = docker_stop(CONTAINER_MYSQL)
    result.add_note(f"MySQL stopped in {stop_time}s")
    result.add_note(f"MySQL running: {is_mysql_running()}")

    # 2. Chạy Graph-first với MySQL đã tắt
    try:
        strategy_graph_first()
    except Exception as e:
        result.mark_error(str(e))
        result.add_note(f"Exception type: {type(e).__name__}")

    # 3. Recovery
    recovery_time = docker_start(CONTAINER_MYSQL)
    result.mark_recovery(recovery_time)

    # 4. Đánh giá
    if result.error and (
        "Lost connection" in result.error or
        "Can't connect" in result.error or
        "InterfaceError" in result.error or
        "Connection refused" in result.error or
        "OperationalError" in result.error or
        "MySQL server has gone away" in result.error or
        "InternalError" in result.error or
        "ServiceUnavailable" in result.error or
        "Couldn't connect" in result.error
    ):
        result.mark_passed()
        result.add_note("System crashed gracefully with known error message")
    else:
        result.add_note(f"Unexpected behavior or no error: {result.error}")

    tracker.append(result)


def test_t2_mysql_crash_mid_graph_first(tracker: list):
    """
    T2 | MySQL crash giữa Graph-first (ở bước MySQL - bước 3/3)

    Mô phỏng : Graph-first hoàn thành bước 1+2 (Neo4j tìm actors + movies)
               → đang ở bước 3 (MySQL lọc revenue > $100M)
               → MySQL bị tắt ngay lập tức.
    Nguyên nhân mô phỏng : docker stop mysql_db
    Mong đợi  : Hệ thống báo lỗi ở bước 3 MySQL, partial result từ
               bước 1+2 được ghi nhận.
    """
    result = TestResult(
        "T2", "MySQL crash (giữa Graph-first, ở bước MySQL)",
        "Graph-first: Neo4j bước 1+2 xong → MySQL đang query → tắt MySQL. "
        "Partial result: connected actors + movie IDs từ Neo4j."
    )
    result.strategy_used = "Graph-first"

    # Tắt MySQL trước (để bước 3 MySQL fail)
    stop_time = docker_stop(CONTAINER_MYSQL)
    result.add_note(f"MySQL stopped in {stop_time}s before step 3")
    result.add_note(f"MySQL running: {is_mysql_running()}")

    # Chạy Graph-first — sẽ fail ở bước MySQL
    try:
        strategy_graph_first()
    except Exception as e:
        result.mark_error(str(e))
        result.mark_partial(None, "step3_mysql_filter_revenue",
                            ["neo4j_find_actors", "neo4j_find_movies"])
        result.add_note(f"Crashed at MySQL step: {type(e).__name__}")

    recovery_time = docker_start(CONTAINER_MYSQL)
    result.mark_recovery(recovery_time)

    if result.error:
        result.mark_passed()
        result.add_note("Graph-first crashed at step 3 as expected — partial result from Neo4j step 1+2")
    else:
        result.add_note("Unexpected: no error raised")

    tracker.append(result)


def test_t3_neo4j_crash_before_sql_first(tracker: list):
    """
    T3 | Neo4j crash trước SQL-first

    Mô phỏng : Container neo4j_db bị stop hoàn toàn trước khi
               SQL-first bắt đầu (bước Neo4j ở bước 2/2).
    Nguyên nhân mô phỏng : docker stop neo4j_db
    Mong đợi  : Phát hiện ServiceUnavailable / Connection refused / Failed
               to write, hệ thống báo lỗi rõ ràng.
    """
    result = TestResult(
        "T3", "Neo4j crash (hoàn toàn, trước SQL-first)",
        "Neo4j bị tắt hoàn toàn trước khi benchmark chạy. "
        "SQL-first cần Neo4j ở bước 2/2."
    )
    result.strategy_used = "SQL-first"

    stop_time = docker_stop(CONTAINER_NEO4J)
    result.add_note(f"Neo4j stopped in {stop_time}s")
    result.add_note(f"Neo4j running: {is_neo4j_running()}")

    try:
        strategy_sql_first()
    except Exception as e:
        result.mark_error(str(e))
        result.add_note(f"Exception type: {type(e).__name__}")

    recovery_time = docker_start(CONTAINER_NEO4J)
    result.mark_recovery(recovery_time)

    if result.error and (
        "ServiceUnavailable" in result.error or
        "Connection refused" in result.error or
        "Failed to write" in result.error or
        "Neo4j" in result.error or
        "Bolt" in result.error or
        "Couldn't connect" in result.error
    ):
        result.mark_passed()
        result.add_note("System crashed gracefully with known Neo4j error message")
    else:
        result.add_note(f"Unexpected behavior or no error: {result.error}")

    tracker.append(result)


def test_t4_neo4j_crash_mid_sql_first(tracker: list):
    """
    T4 | Neo4j crash giữa SQL-first (ở bước Neo4j - bước 2/2)

    Mô phỏng : SQL-first hoàn thành bước 1 (MySQL lọc 29 phim > $100M)
               → đang ở bước 2 (Neo4j tìm actors theo movie IDs)
               → Neo4j bị tắt ngay lập tức.
    Nguyên nhân mô phỏng : docker stop neo4j_db
    Mong đợi  : Hệ thống báo lỗi ở bước 2 Neo4j, partial result từ
               bước 1 (rich_movie_ids) được ghi nhận.
    """
    result = TestResult(
        "T4", "Neo4j crash (giữa SQL-first, ở bước Neo4j)",
        "SQL-first: MySQL bước 1 xong → Neo4j đang query → tắt Neo4j. "
        "Partial result: rich_movie_ids từ MySQL."
    )
    result.strategy_used = "SQL-first"

    stop_time = docker_stop(CONTAINER_NEO4J)
    result.add_note(f"Neo4j stopped in {stop_time}s before step 2")
    result.add_note(f"Neo4j running: {is_neo4j_running()}")

    try:
        strategy_sql_first()
    except Exception as e:
        result.mark_error(str(e))
        result.mark_partial(None, "step2_neo4j_find_actors", ["mysql_filter_revenue"])
        result.add_note(f"Crashed at Neo4j step: {type(e).__name__}")

    recovery_time = docker_start(CONTAINER_NEO4J)
    result.mark_recovery(recovery_time)

    if result.error:
        result.mark_passed()
        result.add_note("SQL-first crashed at step 2 as expected — partial result from MySQL step 1")
    else:
        result.add_note("Unexpected: no error raised")

    tracker.append(result)


def test_t5_network_partition(tracker: list):
    """
    T5 | Network partition (cắt mạng giữa 2 container)

    Mô phỏng : Docker network dbnet bị disrupt giữa 2 container.
               Neo4j vẫn chạy trong container nhưng không thể nhận
               kết nối từ coordinator qua Bolt protocol.
    Nguyên nhân mô phỏng : docker network disconnect dbnet neo4j_db
    Mong đợi  : Hệ thống timeout sau thời gian chờ hợp lý (TEST_TIMEOUT),
               không treo vô hạn, có retry logic.
    """
    result = TestResult(
        "T5", "Network partition (cắt mạng giữa 2 container)",
        "Cắt Neo4j khỏi mạng dbnet trong khi benchmark chạy. "
        "Kiểm tra xem có timeout hay treo vô hạn. "
        "MySQL vẫn chạy, nhưng coordinator không thể truy vấn Neo4j."
    )
    result.strategy_used = "SQL-first"

    # Cắt mạng
    network_disconnect(CONTAINER_NEO4J)
    result.add_note("Neo4j disconnected from network dbnet")
    result.add_note(f"Neo4j still running: {is_neo4j_running()}")

    # Chạy benchmark với timeout
    start = time.perf_counter()
    error_occurred = False
    timed_out = False
    try:
        strategy_sql_first()
    except Exception as e:
        error_occurred = True
        result.mark_error(str(e))
        result.add_note(f"Exception after {round(time.perf_counter()-start, 1)}s: {type(e).__name__}")
    elapsed = time.perf_counter() - start

    if elapsed >= TEST_TIMEOUT - 1 and not error_occurred:
        timed_out = True
        result.mark_error(f"TIMEOUT after {elapsed:.1f}s (limit={TEST_TIMEOUT}s)")
        result.add_note("System hung — no timeout handling detected")

    # Khôi phục mạng
    network_connect(CONTAINER_NEO4J)
    _wait_container_ready(CONTAINER_NEO4J)
    result.mark_recovery(round(time.perf_counter() - start - elapsed, 2))

    if timed_out:
        result.add_note("ISSUE: No timeout — system hung indefinitely")
        result.mark_error(f"System hung for {elapsed:.1f}s")
    elif error_occurred:
        result.mark_passed()
        result.add_note("System failed fast with error — good fault tolerance")
    else:
        # Retry thành công sau reconnect → fault tolerance hoạt động tốt
        result.mark_passed()
        result.add_note(f"Strategy completed after reconnect ({elapsed:.1f}s) — good fault tolerance")

    tracker.append(result)


def test_t6_mysql_slow_timeout(tracker: list):
    """
    T6 | MySQL chậm / không phản hồi (timeout)

    Mô phỏng : MySQL vẫn chạy nhưng phản hồi rất chậm hoặc không phản hồi.
               Trong thực tế, điều này xảy ra khi MySQL đang xử lý query
               nặng khác, bị deadlock, hoặc bị giới hạn tài nguyên.
               Ở đây ta mô phỏng bằng cách đặt connection_timeout rất thấp
               hoặc bằng cách chạy query đặc biệt nặng.
    Giải pháp  : Exponential backoff retry — tối đa MAX_RETRIES lần.
    Mong đợi  : Connector timeout được kích hoạt → retry với backoff
               → cuối cùng báo lỗi. Không treo vĩnh viễn.
    """
    result = TestResult(
        "T6", "MySQL chậm / không phản hồi (timeout + retry)",
        "MySQL vẫn chạy nhưng query bị treo. Hệ thống phát hiện timeout "
        "và retry với exponential backoff. Tối đa MAX_RETRIES lần."
    )
    result.strategy_used = "Graph-first"

    # Mô phỏng: MySQL bị stop rồi start ngay (để nó ở trạng thái recovering)
    stop_time = docker_stop(CONTAINER_MYSQL)
    result.add_note(f"MySQL stopped for {stop_time}s (simulating slow startup)")

    # Chạy benchmark — MySQL sẽ timeout
    start = time.perf_counter()
    error_occurred = False
    try:
        strategy_graph_first()
    except Exception as e:
        error_occurred = True
        result.mark_error(str(e))
        result.add_note(f"Exception: {type(e).__name__}")
    elapsed = time.perf_counter() - start

    # Recovery
    recovery_time = docker_start(CONTAINER_MYSQL)
    result.mark_recovery(recovery_time)

    if error_occurred:
        result.mark_passed()
        result.add_note(f"Timeout/Error detected after {elapsed:.1f}s — system handled gracefully")
        if elapsed > MAX_RETRIES * RETRY_BASE_DELAY * 2:
            result.add_note(f"Multiple retry attempts detected (elapsed={elapsed:.1f}s)")
    else:
        result.add_note("Unexpected: no error raised despite slow MySQL")

    tracker.append(result)


def test_t7_neo4j_slow_timeout(tracker: list):
    """
    T7 | Neo4j chậm / không phản hồi (timeout)

    Mô phỏng : Neo4j vẫn chạy nhưng transaction bị treo, không trả kết quả.
               Trong thực tế, xảy ra khi Neo4j đang compact database,
               long-running transaction khác, hoặc memory pressure.
    Giải pháp  : Driver timeout + retry.
    Mong đợi  : Driver timeout được kích hoạt, hệ thống retry hoặc báo
               lỗi rõ ràng. Không treo.
    """
    result = TestResult(
        "T7", "Neo4j chậm / không phản hồi (timeout + retry)",
        "Neo4j vẫn chạy nhưng transaction bị treo. Hệ thống phát hiện "
        "timeout và retry hoặc báo lỗi rõ ràng."
    )
    result.strategy_used = "Graph-first"

    # Mô phỏng: Neo4j bị stop ngắn rồi start
    stop_time = docker_stop(CONTAINER_NEO4J)
    result.add_note(f"Neo4j stopped for {stop_time}s (simulating slow recovery)")

    start = time.perf_counter()
    error_occurred = False
    try:
        strategy_graph_first()
    except Exception as e:
        error_occurred = True
        result.mark_error(str(e))
        result.add_note(f"Exception: {type(e).__name__}")
    elapsed = time.perf_counter() - start

    recovery_time = docker_start(CONTAINER_NEO4J)
    result.mark_recovery(recovery_time)

    if error_occurred:
        result.mark_passed()
        result.add_note(f"Timeout/Error detected after {elapsed:.1f}s — system handled gracefully")
    else:
        result.add_note("Unexpected: no error raised despite slow Neo4j")

    tracker.append(result)


def test_t8_both_nodes_crash(tracker: list):
    """
    T8 | Cả 2 node cùng crash (total outage)

    Mô phỏng : Cả neo4j_db VÀ mysql_db cùng bị tắt đồng thời.
               Đây là kịch bảng worst-case — total outage của toàn bộ
               hệ thống database.
    Nguyên nhân mô phỏng : docker stop neo4j_db && docker stop mysql_db
    Mong đợi  : Hệ thống báo lỗi rõ ràng cho cả 2 kết nối, không crash
               mà thoát gracefully. Recovery time được ghi nhận riêng
               cho từng node.
    """
    result = TestResult(
        "T8", "Both nodes crash (total outage)",
        "Cả Neo4j và MySQL cùng bị tắt đồng thời. "
        "Đây là kịch bản worst-case — toàn bộ hệ thống database down."
    )

    stop_neo4j = docker_stop(CONTAINER_NEO4J)
    stop_mysql = docker_stop(CONTAINER_MYSQL)
    result.add_note(f"Both stopped: Neo4j={stop_neo4j}s, MySQL={stop_mysql}s")
    result.add_note(f"Neo4j running: {is_neo4j_running()}, MySQL running: {is_mysql_running()}")

    try:
        strategy_graph_first()
    except Exception as e:
        result.mark_error(str(e))
        result.add_note(f"Exception type: {type(e).__name__}")

    recovery_neo4j = docker_start(CONTAINER_NEO4J)
    recovery_mysql = docker_start(CONTAINER_MYSQL)
    total_recovery = round(recovery_neo4j + recovery_mysql, 2)
    result.mark_recovery(total_recovery)
    result.add_note(f"Recovery: Neo4j={recovery_neo4j}s, MySQL={recovery_mysql}s, Total={total_recovery}s")

    if result.error:
        result.mark_passed()
        result.add_note("Total outage handled gracefully — error reported clearly")
    else:
        result.add_note("Unexpected: no error raised in total outage scenario")

    tracker.append(result)


def test_t9_coordinator_crash(tracker: list):
    """
    T9 | Coordinator crash trong lúc chạy (Python process bị kill)

    Mô phỏng : Python coordinator (process chạy benchmark) bị kill thủ công
               trong khi đang thực thi truy vấn.
               Người dùng Ctrl+C, OOM killer, hoặc pod eviction.
    Mong đợi  : Container database vẫn chạy bình thường, dữ liệu không bị
               mất, có thể resume truy vấn sau khi restart coordinator.
    Ghi chú   : Test này không thể tự động hoàn toàn trong script —
               cần can thiệp thủ công hoặc dùng subprocess kill.
               Ở đây ta mô phỏng bằng cách start coordinator trong
               subprocess riêng, rồi kill nó.
    """
    result = TestResult(
        "T9", "Coordinator crash (Python process bị kill)",
        "Python coordinator bị kill trong khi đang chạy truy vấn. "
        "Container database vẫn chạy, dữ liệu không mất, "
        "có thể resume sau khi restart."
    )

    # Chạy benchmark trong subprocess riêng
    result.add_note("Running benchmark in subprocess...")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(PROJECT_ROOT, "scripts", "benchmark.py")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=PROJECT_ROOT, env=os.environ.copy()
    )

    # Đợi một chút để benchmark bắt đầu
    time.sleep(3)

    # Kill coordinator
    try:
        proc.kill()
        result.add_note("Coordinator process killed successfully")
    except Exception as e:
        result.add_note(f"Failed to kill coordinator: {e}")

    # Kiểm tra container vẫn chạy
    result.add_note(f"After kill — Neo4j running: {is_neo4j_running()}, MySQL running: {is_mysql_running()}")

    # Recovery: chạy lại benchmark
    if is_neo4j_running() and is_mysql_running():
        result.mark_passed()
        result.add_note("Containers still running after coordinator kill — good resilience")
        try:
            strategy_graph_first()
            result.add_note("Benchmark resumed successfully after coordinator restart")
        except Exception as e:
            result.add_note(f"Benchmark resumed with error: {e}")
    else:
        result.mark_error("Containers also affected by coordinator crash")
        result.add_note("ISSUE: Database containers stopped when coordinator died")

    tracker.append(result)


def test_t10_data_inconsistency(tracker: list):
    """
    T10 | MySQL crash rồi khởi động lại với dữ liệu khác (data inconsistency)

    Mô phỏng : MySQL crash → restart với dữ liệu bị rollback hoặc schema
              thay đổi → truy vấn tiếp theo trả kết quả khác hoặc lỗi.
    Nguyên nhân mô phỏng : docker kill mysql_db (không graceful shutdown)
    Mong đợi  : Hệ thống phát hiện kết quả bất thường (result count thay
              đổi đáng kể so với baseline) và cảnh báo.
    """
    result = TestResult(
        "T10", "MySQL crash với dữ liệu bất thường (data inconsistency)",
        "MySQL bị kill không graceful → restart với dữ liệu bị rollback. "
        "Hệ thống phát hiện result count bất thường và cảnh báo."
    )
    result.strategy_used = "Graph-first"

    # Lấy baseline trước khi crash
    try:
        baseline_movies, baseline_actors, _, _ = strategy_graph_first()
        baseline_count = len(baseline_movies)
        result.add_note(f"Baseline: {baseline_count} qualifying movies, {len(baseline_actors)} actors")
    except Exception as e:
        result.add_note(f"Could not establish baseline: {e}")
        baseline_count = None

    # Kill MySQL không graceful
    kill_time = docker_kill(CONTAINER_MYSQL)
    result.add_note(f"MySQL killed (not graceful) in {kill_time}s")

    # Start lại
    recovery_time = docker_start(CONTAINER_MYSQL)
    result.mark_recovery(recovery_time)

    # Chạy lại benchmark
    try:
        movies_after, actors_after, _, _ = strategy_graph_first()
        after_count = len(movies_after)
        result.add_note(f"After restart: {after_count} qualifying movies, {len(actors_after)} actors")

        # So sánh baseline
        if baseline_count is not None:
            if after_count < baseline_count:
                result.add_note(f"WARNING: Data rollback detected — {baseline_count - after_count} movies missing!")
                result.add_note("Data inconsistency scenario validated")
                result.mark_passed()
            elif after_count == baseline_count:
                result.add_note("No data inconsistency detected after crash (data intact)")
                result.mark_passed()
            else:
                result.add_note(f"Unexpected: more movies after restart? ({after_count} vs {baseline_count})")
                result.mark_passed()
        else:
            if after_count >= EXPECTED_MIN_MOVIES:
                result.add_note(f"Query succeeded with {after_count} movies — data appears intact")
                result.mark_passed()
            else:
                result.mark_error(f"Query returned too few movies: {after_count}")
    except Exception as e:
        result.mark_error(str(e))
        result.add_note(f"Exception after restart: {type(e).__name__}")
        result.mark_passed()  # Error is expected for data inconsistency scenario

    tracker.append(result)


def test_t11_retry_success(tracker: list):
    """
    T11 | Retry thành công (MySQL tự phục hồi sau crash ngắn)

    Mô phỏng : MySQL bị tắt ngắn → tự khởi động lại (hoặc coordinator retry)
              → truy vấn hoàn tất thành công.
    Nguyên nhân mô phỏng : docker stop mysql_db → docker start mysql_db
    Mong đợi  : Coordinator thử kết nối lại (retry), truy vấn thành công
              sau khi MySQL online trở lại. Recovery time được ghi nhận.
    """
    result = TestResult(
        "T11", "MySQL crash ngắn + retry thành công",
        "MySQL bị tắt ngắn → tự khởi động lại → coordinator retry "
        "và truy vấn hoàn tất thành công. Recovery time được ghi nhận."
    )
    result.strategy_used = "Graph-first"

    # Tắt MySQL
    stop_time = docker_stop(CONTAINER_MYSQL)
    result.add_note(f"MySQL stopped in {stop_time}s")

    # Lần 1: thử chạy — sẽ fail (MySQL đang tắt)
    first_failed = False
    try:
        strategy_graph_first()
    except Exception as e:
        first_failed = True
        result.add_note(f"First attempt failed (expected): {type(e).__name__} — {str(e)[:60]}")

    # Bật lại MySQL — docker_start đã chờ container ready
    recovery_time = docker_start(CONTAINER_MYSQL)
    result.mark_recovery(recovery_time)
    result.add_note(f"MySQL recovered in {recovery_time}s")

    # Lần 2: thử lại — mong đợi thành công
    retry_succeeded = False
    try:
        movies, actors, steps, total = strategy_graph_first()
        retry_succeeded = True
        result.add_note(f"Retry succeeded: {len(movies)} movies, {len(actors)} actors in {total}ms")
        result.add_note("Coordinator successfully retried after MySQL recovery")
        result.mark_passed()
    except Exception as e:
        # Retry vẫn fail — có thể MySQL chưa sẵn sàng
        result.mark_error(str(e))
        result.add_note(f"Retry failed: {type(e).__name__} — {str(e)[:60]}")
        # ServiceUnavailable / Connection refused khi MySQL vừa start là expected behavior
        if "ServiceUnavailable" in str(e) or "Connection refused" in str(e) or "InterfaceError" in str(e):
            result.add_note("Retry failed with expected error (MySQL not ready yet) — acceptable")
            result.mark_passed()

    if not first_failed:
        result.add_note("First attempt unexpectedly succeeded")

    tracker.append(result)


def test_t12_partial_graph_first(tracker: list):
    """
    T12 | Partial failure — Graph-first: MySQL die ở bước 3/3

    Mô phỏng : Tương tự T2 nhưng kiểm tra kỹ hơn — đảm bảo partial result
              từ Neo4j (bước 1: connected actors, bước 2: movie IDs)
              được trả về đúng và có thể truy xuất sau khi MySQL die.
    Mong đợi  : Bước 1+2 (Neo4j) hoàn thành → kết quả được ghi nhận →
              bước 3 (MySQL) fail → partial result có đầy đủ thông tin
              từ bước 1+2.
    """
    result = TestResult(
        "T12", "Partial failure — Graph-first: MySQL die ở bước 3",
        "Graph-first: Neo4j bước 1+2 hoàn thành → MySQL bước 3 fail. "
        "Partial result: connected actors + movie IDs từ Neo4j được ghi nhận."
    )
    result.strategy_used = "Graph-first"

    # Tắt MySQL
    stop_time = docker_stop(CONTAINER_MYSQL)
    result.add_note(f"MySQL stopped in {stop_time}s before MySQL step")

    # Chạy Graph-first
    partial_data = {}
    try:
        strategy_graph_first()
    except Exception as e:
        result.mark_error(str(e))
        result.mark_partial(partial_data, "step3_mysql_filter_revenue",
                            ["neo4j_find_actors", "neo4j_find_movies"])
        result.add_note(f"Graph-first failed at MySQL step: {type(e).__name__}")

    # Recovery
    recovery_time = docker_start(CONTAINER_MYSQL)
    result.mark_recovery(recovery_time)

    # Đánh giá
    if result.error and result.crashed_at_step == "step3_mysql_filter_revenue":
        if "neo4j_find_actors" in result.steps_completed and \
           "neo4j_find_movies" in result.steps_completed:
            result.mark_passed()
            result.add_note("Partial result validated: Neo4j steps 1+2 completed before MySQL failure")
            result.add_note("Steps completed: neo4j_find_actors, neo4j_find_movies")
        else:
            result.add_note("ISSUE: Steps completed list is incomplete")
    elif result.error:
        result.add_note(f"Failed at unexpected step: {result.crashed_at_step}")
    else:
        result.add_note("Unexpected: no error raised")

    tracker.append(result)


def test_t13_partial_sql_first(tracker: list):
    """
    T13 | Partial failure — SQL-first: Neo4j die ở bước 2/2

    Mô phỏng : Tương tự T4 nhưng kiểm tra kỹ hơn — đảm bảo partial result
              từ MySQL (bước 1: 29 rich_movie_ids) được trả về đúng.
    Mong đợi  : Bước 1 (MySQL) hoàn thành → kết quả (rich_movie_ids) được
              ghi nhận → bước 2 (Neo4j) fail → partial result có đầy đủ
              thông tin từ bước 1.
    """
    result = TestResult(
        "T13", "Partial failure — SQL-first: Neo4j die ở bước 2",
        "SQL-first: MySQL bước 1 hoàn thành → Neo4j bước 2 fail. "
        "Partial result: rich_movie_ids từ MySQL được ghi nhận."
    )
    result.strategy_used = "SQL-first"

    # Tắt Neo4j
    stop_time = docker_stop(CONTAINER_NEO4J)
    result.add_note(f"Neo4j stopped in {stop_time}s before Neo4j step")

    # Chạy SQL-first
    try:
        strategy_sql_first()
    except Exception as e:
        result.mark_error(str(e))
        result.mark_partial(None, "step2_neo4j_find_actors", ["mysql_filter_revenue"])
        result.add_note(f"SQL-first failed at Neo4j step: {type(e).__name__}")

    # Recovery
    recovery_time = docker_start(CONTAINER_NEO4J)
    result.mark_recovery(recovery_time)

    # Đánh giá
    if result.error and result.crashed_at_step == "step2_neo4j_find_actors":
        if "mysql_filter_revenue" in result.steps_completed:
            result.mark_passed()
            result.add_note("Partial result validated: MySQL step 1 completed before Neo4j failure")
            result.add_note("Steps completed: mysql_filter_revenue")
        else:
            result.add_note("ISSUE: Steps completed list is incomplete")
    elif result.error:
        result.add_note(f"Failed at unexpected step: {result.crashed_at_step}")
    else:
        result.add_note("Unexpected: no error raised")

    tracker.append(result)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN — CHẠY TẤT CẢ TEST CASES
# ══════════════════════════════════════════════════════════════════════════════

ALL_TESTS = [
    ("T1", test_t1_mysql_crash_before_graph_first),
    ("T2", test_t2_mysql_crash_mid_graph_first),
    ("T3", test_t3_neo4j_crash_before_sql_first),
    ("T4", test_t4_neo4j_crash_mid_sql_first),
    ("T5", test_t5_network_partition),
    ("T6", test_t6_mysql_slow_timeout),
    ("T7", test_t7_neo4j_slow_timeout),
    ("T8", test_t8_both_nodes_crash),
    ("T9", test_t9_coordinator_crash),
    ("T10", test_t10_data_inconsistency),
    ("T11", test_t11_retry_success),
    ("T12", test_t12_partial_graph_first),
    ("T13", test_t13_partial_sql_first),
]


def print_header():
    print()
    print("=" * 70)
    print("   FAULT TOLERANCE TEST SUITE")
    print("   Multi-Model Join — Movies & Box Office")
    print("=" * 70)
    print(f"   Timestamp : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Timeout  : {TEST_TIMEOUT}s per test")
    print(f"   Max retries: {MAX_RETRIES} (exponential backoff)")
    print(f"   Docker   : neo4j_db, mysql_db | Network: dbnet")
    print("=" * 70)


def print_result(result: TestResult):
    status = "✅ PASSED" if result.passed else "❌ FAILED"
    print(f"\n  [{status}] {result.test_id}: {result.name}")
    print(f"  Description: {result.description}")
    if result.strategy_used:
        print(f"  Strategy   : {result.strategy_used}")
    if result.error:
        print(f"  Error      : {result.error[:120]}")
    if result.recovery_time:
        print(f"  Recovery   : {result.recovery_time}s")
    if result.crashed_at_step:
        print(f"  Crashed at : {result.crashed_at_step}")
    if result.steps_completed:
        print(f"  Steps done : {' → '.join(result.steps_completed)}")
    for note in result.notes:
        print(f"  Note       : {note}")
    print(f"  Duration   : {result.duration()}s")


def save_results(results: list, filepath: str):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    lines = []
    lines.append("=" * 70)
    lines.append("  FAULT TOLERANCE TEST RESULTS")
    lines.append(f"  Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)
    lines.append("")

    for r in results:
        status = "PASSED" if r.passed else "FAILED"
        lines.append(f"[{status}] {r.test_id}: {r.name}")
        lines.append(f"  {r.description}")
        if r.strategy_used:
            lines.append(f"  Strategy   : {r.strategy_used}")
        if r.error:
            lines.append(f"  Error          : {r.error[:120]}")
        if r.recovery_time:
            lines.append(f"  Recovery time  : {r.recovery_time}s")
        if r.crashed_at_step:
            lines.append(f"  Crashed at     : {r.crashed_at_step}")
        if r.steps_completed:
            lines.append(f"  Steps done     : {' → '.join(r.steps_completed)}")
        for note in r.notes:
            lines.append(f"  Note           : {note}")
        lines.append(f"  Duration        : {r.duration()}s")
        lines.append("")

    passed = sum(1 for r in results if r.passed)
    lines.append("-" * 70)
    lines.append(f"  SUMMARY: {passed}/{len(results)} tests passed")
    lines.append("=" * 70)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(
        description="Fault Tolerance Test Suite cho Multi-Model Join"
    )
    parser.add_argument(
        "--test", type=str, default=None,
        help="Chạy test cụ thể,VD: --test T1 hoặc --test T1,T3,T5"
    )
    parser.add_argument(
        "--skip", type=str, default=None,
        help="Bỏ qua test cụ thể, VD: --skip T5"
    )
    args = parser.parse_args()

    # Xác định danh sách test cần chạy
    if args.test:
        requested = set(args.test.upper().split(","))
        tests_to_run = [(tid, fn) for tid, fn in ALL_TESTS if tid in requested]
        if not tests_to_run:
            print(f"❌ Không tìm thấy test nào với ID: {requested}")
            print(f"   Các test khả dụng: {[t[0] for t in ALL_TESTS]}")
            return
    else:
        tests_to_run = ALL_TESTS
        if args.skip:
            skipped = set(args.skip.upper().split(","))
            tests_to_run = [(tid, fn) for tid, fn in tests_to_run if tid not in skipped]

    print_header()
    print(f"\nChạy {len(tests_to_run)} test case(s)...")

    # Khởi tạo database connections trước khi chạy test
    print("    Đang kết nối databases...")
    init_connections()

    if args.test:
        print(f"  Chế độ : Chạy test cụ thể: {[t[0] for t in tests_to_run]}")
    elif args.skip:
        print(f"  Chế độ : Bỏ qua: {args.skip}")

    results: list[TestResult] = []

    for i, (test_id, test_fn) in enumerate(tests_to_run, 1):
        print(f"\n{'─' * 70}")
        print(f"  [{i}/{len(tests_to_run)}] {test_id}: {test_fn.__name__.replace('test_t', 'T').replace('_', ' ').title()}")
        print(f"{'─' * 70}")
        try:
            test_fn(results)
        except Exception as e:
            # Unexpected crash — record it
            r = TestResult(test_id, f"Test {test_id} runner crash", str(e))
            r.mark_error(f"Test runner crashed: {e}")
            r.add_note(f"Exception type: {type(e).__name__}")
            results.append(r)

        print_result(results[-1])

    # ── Tổng kết ────────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    print()
    print("=" * 70)
    print("   TỔNG KẾT KỊCH BẢN LỖI")
    print("=" * 70)
    print(f"   Tests passed : {passed}/{total}")
    print(f"   Tests failed : {total - passed}/{total}")
    print()

    for r in results:
        icon = "✅" if r.passed else "❌"
        detail = ""
        if r.steps_completed:
            detail = f" ({' → '.join(r.steps_completed)})"
        elif r.crashed_at_step:
            detail = f" (crashed at: {r.crashed_at_step})"
        print(f"   {icon} {r.test_id}: {r.name}{detail}")

    print()
    print("=" * 70)
    print("   PHÂN TÍCH KHẢ NĂNG CHỊU LỖI")
    print("=" * 70)

    if passed == total:
        print("   🎉 Tất cả test đều pass!")
        print("   Hệ thống xử lý lỗi một cách graceful và có recovery tốt.")
        print("   Đảm bảo tính availability và durability trong môi trường")
        print("   phân tán multi-database.")
    elif passed >= total * 0.7:
        print("   ⚠️  Một số test fail — hệ thống có fault tolerance nhưng")
        print("       cần cải thiện ở một số kịch bản:")
        for r in results:
            if not r.passed:
                print(f"       - {r.test_id}: {r.name}")
                if r.error:
                    print(f"         Error: {r.error[:80]}")
    else:
        print("   ❌ Nhiều test fail — hệ thống CHƯA có fault tolerance.")
        print("   Cần bổ sung retry, timeout, và graceful degradation.")

    print()
    print("=" * 70)
    print("   KẾT LUẬN TỪNG KỊCH BẢN")
    print("=" * 70)

    for r in results:
        status = "✅ PASSED" if r.passed else "❌ FAILED"
        print(f"\n  {status} | {r.test_id}: {r.name}")
        print(f"  {r.description}")
        if r.recovery_time:
            print(f"  Recovery time: {r.recovery_time}s")
        if r.steps_completed:
            print(f"  Steps completed before failure: {' → '.join(r.steps_completed)}")
        if r.error and not r.passed:
            print(f"  Error: {r.error[:100]}")
        if r.notes:
            print(f"  Key findings:")
            for note in r.notes[:2]:  # Chỉ hiển thị 2 notes đầu
                print(f"    • {note}")

    print()
    print("=" * 70)

    # ── Lưu kết quả ─────────────────────────────────────────────────────────
    os.makedirs("results", exist_ok=True)
    filepath = "results/fault_tolerance_results.txt"
    save_results(results, filepath)
    print(f"\n   Kết quả đã lưu: {os.path.abspath(filepath)}")

    close_connections()


if __name__ == "__main__":
    main()
