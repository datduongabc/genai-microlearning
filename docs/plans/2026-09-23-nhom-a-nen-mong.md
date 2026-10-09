# Nhóm A - Nền móng - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Khôi phục ba thứ đang chặn mọi việc khác: kho mã có kiểm soát và không rò khóa, migrations chạy được, và chức năng xuất bản Moodle.

**Architecture:** Ba nhánh độc lập, mỗi nhánh một merge request. Task 1 tạo kho mã nên phải chạy trước. Task 2 và Task 3 độc lập với nhau, có thể làm song song sau Task 1.

**Tech Stack:** Git, Python 3.12 với uv, Alembic, SQLAlchemy, PostgreSQL 16 với pgvector, Moodle 5.2 (PHP 8.3), Docker Compose.

**Spec:** `docs/specs/2026-09-23-kien-truc-gd2-design.md`

## Global Constraints

Không dùng dấu gạch dài trong bất kỳ văn bản nào, dùng dấu gạch ngắn.

Thông điệp commit theo Conventional Commits, không thêm dòng co-author.

Test viết trước phần cài đặt, theo hợp đồng của tính năng chứ không theo mã đã viết.

Trước khi mở merge request: `uv run ruff check .` sạch và toàn bộ `uv run pytest` xanh, không chỉ riêng test của nhánh đó.

Comment giải thích lý do, không mô tả lại mã.

Mọi lệnh Python chạy trong `backend/` bằng `uv run`.

Biến môi trường cho test nằm sẵn trong môi trường phát triển, cơ sở dữ liệu test là `ci_test_db` trên `127.0.0.1`, không dùng `localhost`.

Không bao giờ in giá trị biến môi trường chứa khóa hay mật khẩu ra màn hình, chỉ kiểm tra theo tên và độ dài.

---

## Task 1: Khởi tạo kho mã an toàn

Nhánh `chore/git-init`. Đây là task rủi ro nhất vì một lần commit sai là khóa API lọt vào lịch sử vĩnh viễn.

**Files:**
- Create: `.gitignore`
- Create: `.gitattributes`
- Create: `README.md`
- Create: `docker-compose.yml`
- Create: `devops/.env.example`
- Create: `backend/tests/unit/test_repo_hygiene.py`
- Delete: `schema.sql`

**Interfaces:**
- Consumes: không có, đây là task đầu tiên
- Produces: kho git có nhánh `main`, hàm test `_tracked_files()` trong `backend/tests/unit/test_repo_hygiene.py` trả về `set[str]` các đường dẫn tương đối mà git đang theo dõi

- [ ] **Step 1: Kiểm tra chưa có kho git và ghi lại hiện trạng**

```bash
cd /d/do-an
test -d .git && echo "DA CO GIT - DUNG LAI" || echo "chua co git, tiep tuc"
ls -a | grep -v "^\.$\|^\.\.$"
```

Kỳ vọng: in ra "chua co git, tiep tuc" và danh sách thư mục gốc.
Nếu đã có `.git` thì dừng lại và hỏi người dùng, vì kế hoạch này giả định kho mã chưa tồn tại.

- [ ] **Step 2: Viết `.gitignore` ở thư mục gốc**

Tạo `/d/do-an/.gitignore`:

```gitignore
# Bi mat. devops/.env chua khoa that cua Gemini, Anthropic, OpenAI, SECRET_KEY,
# mat khau co so du lieu va MOODLE_TOKEN, nen phai chan o day truoc khi git init.
.env
*.env
!*.env.example

# Ma nguon Moodle la san pham ben thu ba, 61.556 tep va 1,42 GB.
# README huong dan tai ve va giai nen vao thu muc nay.
/moodle/

# Tai lieu mon hoc dung de thu nghiem, 151 MB ke ca video.
/documents/

# Phu thuoc va ket qua build
.venv/
node_modules/
dist/
__pycache__/
*.py[cod]
*.egg-info/

# Cache cong cu
.pytest_cache/
.ruff_cache/
.mypy_cache/

# Du lieu chay
/backend/uploads/
*.log

# Trinh soan thao va he dieu hanh
.idea/
.vscode/
.DS_Store
Thumbs.db
```

- [ ] **Step 3: Viết `.gitattributes`**

Tạo `/d/do-an/.gitattributes`:

```gitattributes
# Kho mã được phát triển trên Windows nhưng chạy trong container Linux.
# Chuẩn hóa về LF khi lưu để script shell và tệp PHP không bị CRLF làm hỏng.
* text=auto eol=lf

*.sh text eol=lf
*.php text eol=lf
*.py text eol=lf

*.png binary
*.jpg binary
*.pdf binary
*.pptx binary
*.mp3 binary
*.mp4 binary
```

- [ ] **Step 4: Viết test vệ sinh kho mã (test này phải fail trước)**

Tạo `/d/do-an/backend/tests/unit/test_repo_hygiene.py`:

```python
"""Kho mã không được chứa bí mật hay cây mã của bên thứ ba.

Một lần commit tệp .env là khóa API nằm trong lịch sử git vĩnh viễn, nên việc này
được kiểm tra tự động thay vì trông chờ vào trí nhớ của người commit."""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        pytest.skip("chưa có kho git")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def test_khong_co_tep_env_nao_duoc_theo_doi():
    tracked = _tracked_files()

    leaked = {p for p in tracked if Path(p).name == ".env" or p.endswith(".env")}

    assert leaked == set(), f"tệp bí mật bị theo dõi: {sorted(leaked)}"


def test_tep_env_example_van_duoc_theo_doi():
    # Bản mẫu không chứa giá trị thật và là tài liệu duy nhất nói rõ cần biến nào.
    assert "devops/.env.example" in _tracked_files()


def test_ma_nguon_moodle_khong_nam_trong_kho():
    tracked = _tracked_files()

    assert not [p for p in tracked if p.startswith("moodle/")]


def test_tai_lieu_mon_hoc_khong_nam_trong_kho():
    tracked = _tracked_files()

    assert not [p for p in tracked if p.startswith("documents/")]


def test_khong_co_thu_muc_phu_thuoc_nao_duoc_theo_doi():
    tracked = _tracked_files()
    rac = [p for p in tracked if "/node_modules/" in p or "/.venv/" in p or "__pycache__" in p]

    assert rac == []


def test_kho_ma_khong_phinh_qua_muc():
    # Toàn bộ mã nguồn của dự án vào khoảng vài nghìn tệp. Nếu vượt xa con số này
    # thì gần như chắc chắn một thư mục phụ thuộc đã lọt vào.
    assert len(_tracked_files()) < 2000
```

- [ ] **Step 5: Chạy test để xác nhận nó bỏ qua vì chưa có git**

```bash
cd /d/do-an/backend
uv run pytest tests/unit/test_repo_hygiene.py -v
```

Kỳ vọng: cả 6 test đều SKIPPED với lý do "chưa có kho git".

- [ ] **Step 6: Khởi tạo kho và kiểm tra khô trước khi thêm tệp**

```bash
cd /d/do-an
git init
git add -A --dry-run > /tmp/se-them.txt
wc -l < /tmp/se-them.txt
# Dau ra co dang: add 'devops/.env'
# Phai bat theo dau nhay cuoi dong, khong dung neo $, va phai loai tru .env.example.
grep -c "\.env'" /tmp/se-them.txt || true
grep -c "^add 'moodle/" /tmp/se-them.txt || true
grep -c "^add 'documents/" /tmp/se-them.txt || true
grep -c "node_modules\|\.venv" /tmp/se-them.txt || true
```

Trước khi tin vào các con số trên, kiểm tra ngược để chắc biểu thức thực sự bắt được:

```bash
cd /d/do-an
printf "add 'devops/.env'\nadd 'devops/.env.example'\n" | grep -c "\.env'"
```

Kỳ vọng: in ra `1`, tức là bắt đúng `.env` và bỏ qua `.env.example`.
Nếu in ra `0` hoặc `2` thì biểu thức sai, phải sửa trước khi đi tiếp.

Kỳ vọng: số dòng dưới 2000, và cả ba lệnh `grep -c` đều in ra `0`.
Nếu bất kỳ con số nào khác 0 thì dừng lại, sửa `.gitignore`, chạy lại từ đầu bước này. Tuyệt đối không chạy `git add` thật khi các con số chưa đúng.

- [ ] **Step 7: Xóa `schema.sql` vì alembic là nguồn sự thật duy nhất**

```bash
cd /d/do-an
rm -f schema.sql
```

- [ ] **Step 8: Viết `devops/.env.example`**

Tạo `/d/do-an/devops/.env.example` với tên biến và giá trị rỗng hoặc giá trị mẫu không phải bí mật. Danh sách biến lấy đúng theo tên trong `devops/.env` hiện có, đọc bằng lệnh sau và chỉ lấy tên, không lấy giá trị:

```bash
cd /d/do-an/devops
grep -o "^[A-Z_]*=" .env | sort -u
```

Nội dung tệp mẫu, mỗi dòng một biến, có chú thích cho nhóm:

```bash
# Co so du lieu ung dung
DATABASE_USER=microlearning
DATABASE_PASSWORD=
DATABASE_URL=
DATABASE_URL_DOCKER=

# Hai vai tro han che quyen ma ung dung va worker dung, phuc vu RLS
APP_RUNTIME_DB_USER=app_runtime
APP_RUNTIME_DB_PASSWORD=
APP_RUNTIME_DB_NAME=microlearning
APP_RUNTIME_DATABASE_URL=
APP_RUNTIME_DATABASE_URL_DOCKER=
APP_WORKER_DB_USER=app_worker
APP_WORKER_DB_PASSWORD=
APP_WORKER_DB_NAME=microlearning
APP_WORKER_DATABASE_URL=
APP_WORKER_DATABASE_URL_DOCKER=

# Khoa ky JWT
SECRET_KEY=

# Khoa nha cung cap mo hinh, chi gateway doc
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
LLM_GATEWAY_KEY=

# Moodle
MOODLE_WWWROOT=http://localhost:8081
MOODLE_SITE_NAME=Microlearning LMS
MOODLE_TOKEN=
MOODLE_ADMIN_USER=admin
MOODLE_ADMIN_PASSWORD=
MOODLE_ADMIN_EMAIL=
MOODLE_DB_TYPE=pgsql
MOODLE_DB_HOST=db
MOODLE_DB_PORT=5432
MOODLE_DB_NAME=moodle
MOODLE_DB_USER=moodle
MOODLE_DB_PASSWORD=
```

- [ ] **Step 9: Viết `docker-compose.yml` ở thư mục gốc**

Tạo `/d/do-an/docker-compose.yml`:

```yaml
# Toan bo stack dinh nghia o devops/docker-compose.yml. Tep nay chi include lai,
# de thu muc goc van la thu muc du an: duong dan tuong doi, tep .env va ten
# project (do do ca ten volume) khong doi khi chay `docker compose up -d` tu goc.
include:
  - path: devops/docker-compose.yml
    project_directory: .
    env_file: devops/.env
```

- [ ] **Step 10: Viết `README.md`**

Tạo `/d/do-an/README.md`:

````markdown
# Hệ thống AI tạo sinh mô-đun Microlearning từ tài liệu môn học

Đề tài HK261-DAGD1-033. Sinh viên: Dương Nguyễn Tấn Đạt.

Hệ thống nhận tài liệu môn học của giảng viên (PDF, PowerPoint, HTML, video), truy xuất bằng RAG,
rồi sinh mô-đun Microlearning gồm bảy thành phần: Learning Objective, Content, Slides, Example,
Practice, Quiz, Explanation. Mô-đun được xuất bản sang Moodle cho học viên học.

Ứng dụng này chỉ dành cho giảng viên. Học viên không đăng nhập vào đây mà học trên Moodle.

## Tài liệu

- Đề tài: `docs/de-tai.md`
- Thiết kế kiến trúc Giai đoạn 2: `docs/specs/2026-09-23-kien-truc-gd2-design.md`
- Kế hoạch thực thi: `docs/plans/`

## Yêu cầu

Docker Desktop, và mã nguồn Moodle 5.2 tải riêng (xem bước 2).

## Chạy hệ thống

1. Sao chép biến môi trường rồi điền giá trị thật:

```bash
cp devops/.env.example devops/.env
```

2. Tải mã nguồn Moodle 5.2 và giải nén vào thư mục `moodle/` ở gốc kho mã.
   Thư mục này không nằm trong kho vì nặng 1,42 GB và là sản phẩm bên thứ ba.
   Sau khi giải nén, `moodle/public/version.php` phải tồn tại.

3. Dựng toàn bộ stack:

```bash
docker compose up -d
```

- Giao diện: http://localhost:5173
- API: http://localhost:8000
- Moodle: http://localhost:8081

## Phát triển

```bash
cd backend
uv run pytest
uv run ruff check .
uv run alembic upgrade head
```

```bash
cd frontend
npm run test
npm run build
```

## Quy trình đóng góp

Nhánh `main` luôn xanh và không nhận commit trực tiếp. Mỗi tính năng làm trên một nhánh riêng
theo tiền tố `feat/`, `fix/`, `refactor/` hoặc `chore/`, xong thì mở merge request, CI xanh mới
được gộp, và gộp theo kiểu squash.
````

- [ ] **Step 11: Thêm tệp thật và kiểm tra lần cuối trước khi commit**

```bash
cd /d/do-an
git add -A
git status --short | wc -l
git diff --cached --name-only | grep -c "\.env$" || true
git diff --cached --name-only | grep -c "^moodle/" || true
git diff --cached --name-only | grep -c "^documents/" || true
```

Kỳ vọng: tổng số tệp dưới 2000, và cả ba lệnh đếm in ra `0`.
Nếu khác 0 thì chạy `git rm -r --cached <đường dẫn>`, sửa `.gitignore`, rồi lặp lại bước này.

- [ ] **Step 12: Commit nền**

```bash
cd /d/do-an
git commit -q -m "chore: khởi tạo kho mã với mã nguồn hiện có

Kho mã cũ đã mất cùng toàn bộ lịch sử. Commit này chụp lại trạng thái đang chạy
được của backend, frontend và devops, kèm .gitignore chặn tệp .env, mã nguồn
Moodle 1,42 GB và tài liệu môn học 151 MB khỏi lọt vào kho."
git log --oneline
```

- [ ] **Step 13: Chạy test vệ sinh, giờ phải xanh**

```bash
cd /d/do-an/backend
uv run pytest tests/unit/test_repo_hygiene.py -v
```

Kỳ vọng: 6 test PASSED, không còn SKIPPED.

- [ ] **Step 14: Commit test vệ sinh trên nhánh riêng và mở merge request**

```bash
cd /d/do-an
git checkout -b chore/git-init
git add backend/tests/unit/test_repo_hygiene.py
git commit -q -m "test: chặn tệp bí mật và cây mã bên thứ ba lọt vào kho"
cd /d/do-an/backend && uv run ruff check .
```

Kỳ vọng: ruff báo "All checks passed!".

Sau đó tạo kho trên GitHub, đẩy `main` và nhánh `chore/git-init` lên, rồi mở merge request.

---

## Task 2: Khôi phục migrations

Nhánh `fix/restore-alembic`. CI có bước `alembic upgrade head` nên đang đỏ ngay từ bước này.

Điểm mấu chốt: cơ sở dữ liệu thật có **3 vai trò** (`app_runtime`, `app_worker`, và chủ sở hữu) cùng **11 policy Row Level Security**, mà `alembic revision --autogenerate` **không bao giờ sinh ra được** ba thứ này. Nếu chỉ autogenerate rồi dừng, migration sẽ tạo bảng không có RLS và cách ly dữ liệu giữa các giảng viên biến mất mà không có lỗi nào báo ra.

**Files:**
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/alembic/versions/0001_initial_schema.py`
- Create: `backend/tests/integration/test_initial_migration.py`
- Delete: `backend/tests/test_auth_tables_migration.py`
- Delete: `backend/tests/test_embedding_column_migration.py`

**Interfaces:**
- Consumes: kho git từ Task 1
- Produces: một revision duy nhất có `revision = "0001_initial_schema"` và `down_revision = None`; hàm `upgrade()` tạo toàn bộ schema kèm vai trò, quyền và policy RLS

- [ ] **Step 1: Ghi lại schema thật làm chuẩn đối chiếu**

Container `do-an-db` phải đang chạy và gắn volume `do-an_db_data`.

```bash
cd /d/do-an/devops
docker compose -p do-an up -d db
DBUSER=$(grep "^DATABASE_USER=" .env | head -1 | cut -d= -f2- | tr -d '\r')
docker exec do-an-db pg_dump -U "$DBUSER" -d microlearning --schema-only --no-owner \
  --no-privileges --exclude-table='mdl_*' > /tmp/schema-that.sql
grep -c "^CREATE TABLE" /tmp/schema-that.sql
```

Kỳ vọng: in ra `15`, gồm 14 bảng nghiệp vụ cộng `alembic_version`.

- [ ] **Step 2: Ghi lại vai trò, quyền và policy RLS thật**

```bash
cd /d/do-an/devops
DBUSER=$(grep "^DATABASE_USER=" .env | head -1 | cut -d= -f2- | tr -d '\r')
docker exec do-an-db psql -U "$DBUSER" -d microlearning -At -F"|" \
  -c "select tablename, policyname, qual from pg_policies order by tablename" > /tmp/policies-that.txt
wc -l /tmp/policies-that.txt
docker exec do-an-db psql -U "$DBUSER" -d microlearning -At \
  -c "select rolname from pg_roles where rolname in ('app_runtime','app_worker')"
```

Kỳ vọng: `/tmp/policies-that.txt` có 11 dòng, và hai vai trò `app_runtime`, `app_worker` đều in ra.

Thiết kế RLS là dạng lan theo khóa học: bảng `courses` lọc theo `owner_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid`, các bảng khác lọc theo `course_id IN (SELECT courses.id FROM courses)`, riêng `chat_messages` lọc theo `session_id IN (SELECT chat_sessions.id FROM chat_sessions)`. Ứng dụng đặt ngữ cảnh bằng `SET LOCAL app.current_user_id` trong mỗi giao dịch, xem `backend/app/db.py`.

- [ ] **Step 3: Viết test cho migration (phải fail trước)**

Tạo `/d/do-an/backend/tests/integration/test_initial_migration.py`:

```python
"""Chạy migration lên một cơ sở dữ liệu trắng phải dựng lại đúng schema mà các mô hình khai báo,
kèm cả vai trò và Row Level Security.

Alembic autogenerate không sinh ra vai trò, quyền hay policy RLS. Nếu chúng bị bỏ quên thì
cách ly dữ liệu giữa các giảng viên biến mất mà không có lỗi nào báo ra, nên phải kiểm tra
tường minh ở đây."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import app.models  # noqa: F401  (đăng ký mọi bảng lên Base.metadata)
from app.db import Base

BACKEND = Path(__file__).resolve().parents[1]

CAC_BANG_CO_RLS = {
    "chat_messages", "chat_sessions", "chunks", "courses", "document_sections",
    "documents", "evaluations", "jobs", "module_components", "modules", "questions",
}


@pytest.fixture(scope="module")
def db_trang(tmp_path_factory):
    """Một cơ sở dữ liệu trắng đã chạy `alembic upgrade head`."""
    from app.config import settings

    ten_db = "test_migration_tu_dau"
    goc = settings.database_url.rsplit("/", 1)[0]
    quan_tri = create_engine(f"{goc}/postgres", isolation_level="AUTOCOMMIT")
    with quan_tri.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {ten_db}"))
        conn.execute(text(f"CREATE DATABASE {ten_db}"))

    url = f"{goc}/{ten_db}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, check=True, capture_output=True, timeout=300,
        env={**os.environ, "DATABASE_URL": url},
    )
    engine = create_engine(url)

    yield engine

    engine.dispose()
    with quan_tri.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {ten_db}"))
    quan_tri.dispose()


def test_moi_bang_trong_mo_hinh_deu_duoc_tao(db_trang):
    with db_trang.connect() as conn:
        co_that = {
            r[0] for r in conn.execute(
                text("select tablename from pg_tables where schemaname='public'")
            )
        }

    thieu = set(Base.metadata.tables) - co_that

    assert thieu == set()


def test_cot_embedding_dung_768_chieu(db_trang):
    with db_trang.connect() as conn:
        kieu = conn.execute(text(
            "select format_type(atttypid, atttypmod) from pg_attribute "
            "where attrelid = 'chunks'::regclass and attname = 'embedding'"
        )).scalar()

    assert kieu == "vector(768)"


def test_hai_vai_tro_han_che_quyen_duoc_tao(db_trang):
    with db_trang.connect() as conn:
        vai_tro = {
            r[0] for r in conn.execute(
                text("select rolname from pg_roles where rolname in ('app_runtime','app_worker')")
            )
        }

    assert vai_tro == {"app_runtime", "app_worker"}


def test_moi_bang_da_tenant_deu_bat_row_level_security(db_trang):
    with db_trang.connect() as conn:
        da_bat = {
            r[0] for r in conn.execute(
                text("select relname from pg_class where relrowsecurity")
            )
        }

    assert CAC_BANG_CO_RLS - da_bat == set()


def test_moi_bang_da_tenant_deu_co_policy(db_trang):
    with db_trang.connect() as conn:
        co_policy = {r[0] for r in conn.execute(text("select tablename from pg_policies"))}

    assert CAC_BANG_CO_RLS - co_policy == set()


def test_policy_goc_loc_khoa_hoc_theo_nguoi_dang_dang_nhap(db_trang):
    with db_trang.connect() as conn:
        dieu_kien = conn.execute(text(
            "select qual from pg_policies where tablename='courses'"
        )).scalar()

    assert "owner_id" in dieu_kien and "app.current_user_id" in dieu_kien


def test_app_runtime_doc_ghi_duoc_bang_courses(db_trang):
    with db_trang.connect() as conn:
        quyen = {
            r[0] for r in conn.execute(text(
                "select privilege_type from information_schema.role_table_grants "
                "where grantee='app_runtime' and table_name='courses'"
            ))
        }

    assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= quyen
```

- [ ] **Step 4: Chạy test để xác nhận nó fail**

```bash
cd /d/do-an/backend
uv run pytest tests/integration/test_initial_migration.py -v
```

Kỳ vọng: FAIL ở bước fixture, vì `backend/alembic/` chưa tồn tại nên lệnh alembic thoát khác 0.

- [ ] **Step 5: Dựng lại khung alembic**

```bash
cd /d/do-an/backend
uv run alembic init alembic
```

Lệnh này tạo `alembic/env.py`, `alembic/script.py.mako` và `alembic/versions/`.
Nó cũng ghi đè `alembic.ini`, nên kiểm tra lại và giữ nguyên giá trị `script_location` và `sqlalchemy.url` mà tệp cũ đang dùng.

- [ ] **Step 6: Sửa `alembic/env.py` để đọc mô hình và biến môi trường**

Trong `/d/do-an/backend/alembic/env.py`, thay phần `target_metadata = None` và phần lấy URL bằng:

```python
import os

import app.models  # noqa: F401  (đăng ký mọi bảng lên Base.metadata)
from app.db import Base

target_metadata = Base.metadata


def _duong_dan_co_so_du_lieu() -> str:
    # Ưu tiên biến môi trường để test dựng được cơ sở dữ liệu trắng riêng,
    # còn alembic.ini chỉ là giá trị mặc định khi chạy tay.
    return os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
```

Rồi dùng `_duong_dan_co_so_du_lieu()` ở cả `run_migrations_offline()` và `run_migrations_online()`.

- [ ] **Step 7: Sinh phần schema của migration bằng autogenerate**

```bash
cd /d/do-an/backend
uv run alembic revision --autogenerate -m "initial schema"
ls alembic/versions/
```

Đổi tên tệp vừa sinh thành `0001_initial_schema.py`, và sửa hai dòng đầu thành:

```python
revision = "0001_initial_schema"
down_revision = None
```

- [ ] **Step 8: Đối chiếu từng bảng với schema thật**

```bash
cd /d/do-an/backend
grep -o "op.create_table('[a-z_]*'" alembic/versions/0001_initial_schema.py | sed "s/.*'\(.*\)'/\1/" | sort > /tmp/bang-migration.txt
grep -o "^CREATE TABLE public\.[a-z_]*" /tmp/schema-that.sql | sed 's/.*\.//' | sort | grep -v alembic_version > /tmp/bang-that.txt
diff /tmp/bang-migration.txt /tmp/bang-that.txt
```

Kỳ vọng: `diff` không in ra gì.
Nếu có chênh lệch thì mô hình SQLAlchemy và cơ sở dữ liệu thật đã lệch nhau, phải dừng lại và xử lý chênh lệch đó trước khi đi tiếp.

- [ ] **Step 9: Thêm phần mà autogenerate không sinh được**

Thêm vào cuối hàm `upgrade()` trong `0001_initial_schema.py`:

```python
    # Ba phan duoi day alembic khong tu sinh duoc: extension, vai tro, quyen va RLS.
    # Thieu chung thi bang van duoc tao nhung cach ly du lieu giua cac giang vien
    # bien mat ma khong co loi nao bao ra.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    runtime_pw = os.environ["APP_RUNTIME_DB_PASSWORD"]
    worker_pw = os.environ["APP_WORKER_DB_PASSWORD"]
    for ten, mat_khau in (("app_runtime", runtime_pw), ("app_worker", worker_pw)):
        op.execute(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ten}') THEN "
            f"CREATE ROLE {ten} LOGIN PASSWORD '{mat_khau}'; "
            f"END IF; END $$;"
        )
        op.execute(f"GRANT USAGE ON SCHEMA public TO {ten}")
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ten}"
        )
        op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ten}")

    # Goc cua chuoi lan: khoa hoc thuoc ve nguoi dang dang nhap, dat bang
    # SET LOCAL app.current_user_id trong moi giao dich, xem backend/app/db.py.
    op.execute("ALTER TABLE courses ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY courses_tenant_isolation ON courses USING ("
        "owner_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)"
    )

    # Cac bang con lan theo khoa hoc, nen tu dong thua huong bo loc o tren.
    for bang in (
        "chat_sessions", "chunks", "document_sections", "documents",
        "evaluations", "jobs", "module_components", "modules", "questions",
    ):
        op.execute(f"ALTER TABLE {bang} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {bang}_tenant_isolation ON {bang} "
            f"USING (course_id IN (SELECT courses.id FROM courses))"
        )

    # Tin nhan khong co course_id, nen lan qua phien tro chuyen.
    op.execute("ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY chat_messages_tenant_isolation ON chat_messages "
        "USING (session_id IN (SELECT chat_sessions.id FROM chat_sessions))"
    )
```

Thêm `import os` vào đầu tệp.

Trong hàm `downgrade()`, thêm ở đầu:

```python
    for bang in (
        "chat_messages", "chat_sessions", "chunks", "courses", "document_sections",
        "documents", "evaluations", "jobs", "module_components", "modules", "questions",
    ):
        op.execute(f"DROP POLICY IF EXISTS {bang}_tenant_isolation ON {bang}")
```

- [ ] **Step 10: Chạy test migration, giờ phải xanh**

```bash
cd /d/do-an/backend
uv run pytest tests/integration/test_initial_migration.py -v
```

Kỳ vọng: cả 7 test PASSED.

- [ ] **Step 11: Đóng dấu phiên bản cho cơ sở dữ liệu đang có dữ liệu thật**

Cơ sở dữ liệu trong volume `do-an_db_data` đã có đủ schema và đang ghi `d1f6a3c8e5b2`, là mã của lịch sử migration cũ đã mất. Nếu để nguyên, alembic sẽ không tìm thấy revision đó và báo lỗi.

```bash
cd /d/do-an/devops
DBUSER=$(grep "^DATABASE_USER=" .env | head -1 | cut -d= -f2- | tr -d '\r')
docker exec do-an-db psql -U "$DBUSER" -d microlearning \
  -c "update alembic_version set version_num = '0001_initial_schema'"
docker exec do-an-db psql -U "$DBUSER" -d microlearning -At -c "select version_num from alembic_version"
```

Kỳ vọng: in ra `0001_initial_schema`.

Không chạy `alembic upgrade head` lên cơ sở dữ liệu này, vì schema đã có sẵn rồi.

- [ ] **Step 12: Xóa hai test migration đã hết ý nghĩa**

Hai tệp dưới đây kiểm tra việc chuyển đổi giữa các revision cũ như `c8e4a1f6b3d9`, mà lịch sử đó không còn tồn tại sau khi gộp thành một migration khởi tạo duy nhất.

```bash
cd /d/do-an/backend
rm tests/test_auth_tables_migration.py tests/test_embedding_column_migration.py
```

- [ ] **Step 13: Chạy toàn bộ test và lint**

```bash
cd /d/do-an/backend
uv run ruff check .
uv run pytest
```

Kỳ vọng: ruff sạch, và toàn bộ test xanh trừ `test_moodle_bridge_service.py` vẫn đỏ vì plugin thuộc Task 3.

- [ ] **Step 14: Commit**

```bash
cd /d/do-an
git checkout -b fix/restore-alembic
git add backend/alembic backend/alembic.ini backend/tests/integration/test_initial_migration.py
git add -u backend/tests
git commit -q -m "fix(db): khôi phục migrations thành một revision khởi tạo duy nhất

Thư mục alembic đã mất nên bước alembic upgrade head trong CI luôn đỏ.
Revision mới dựng lại toàn bộ schema từ các mô hình, kèm extension vector,
hai vai trò hạn chế quyền và 11 policy Row Level Security, là những thứ
autogenerate không sinh ra được. Hai test kiểm tra chuyển đổi giữa các
revision cũ bị xóa vì lịch sử đó không còn."
```

---

## Task 3: Khôi phục plugin Moodle

Nhánh `fix/restore-moodle-plugin`. Backend vẫn gọi `local_lms_bridge_create_book` và `local_lms_bridge_create_quiz` nhưng mã plugin không còn, nên chức năng xuất bản hỏng ở mức mã nguồn.

Test `backend/tests/unit/test_moodle_bridge_service.py` đã tồn tại và đang đỏ. Nó đọc `moodle-plugin/local_lms_bridge/db/services.php` rồi so với các hàm mà `moodle_client.py` gọi. Đây chính là test dẫn đường, không cần viết test mới cho phần hợp đồng.

**Files:**
- Create: `moodle-plugin/local_lms_bridge/version.php`
- Create: `moodle-plugin/local_lms_bridge/db/services.php`
- Create: `moodle-plugin/local_lms_bridge/classes/external/create_book.php`
- Create: `moodle-plugin/local_lms_bridge/classes/external/create_quiz.php`
- Create: `moodle-plugin/local_lms_bridge/lang/en/local_lms_bridge.php`
- Modify: `devops/docker-compose.yml:31`

**Interfaces:**
- Consumes: kho git từ Task 1
- Produces: hai hàm web service. `local_lms_bridge_create_book(courseid int, name string, chapters list, sectionnum int)` trả về `int` là cmid. `local_lms_bridge_create_quiz(courseid int, name string, questions list, sectionnum int)` trả về `int` là cmid. Mỗi phần tử `chapters` gồm `title string`, `content string` là HTML, `subchapter int` nhận 0 hoặc 1. Mỗi phần tử `questions` gồm `stem string`, `choices list of string`, `correct_index int`, `explanation string` không bắt buộc.

- [ ] **Step 1: Chạy test dẫn đường để thấy nó đỏ**

```bash
cd /d/do-an/backend
uv run pytest tests/unit/test_moodle_bridge_service.py -v
```

Kỳ vọng: các test đọc tệp plugin FAIL vì `moodle-plugin/local_lms_bridge/db/services.php` không tồn tại.

- [ ] **Step 2: Liệt kê chính xác các hàm backend đang gọi**

```bash
cd /d/do-an/backend
grep -o 'call(\s*"[a-z_0-9]*"' app/services/moodle_client.py | sed 's/.*"\(.*\)"/\1/' | sort -u
```

Kỳ vọng: in ra danh sách gồm `core_course_create_courses`, `core_course_get_contents`, `core_course_get_courses`, `core_course_search_courses`, `local_lms_bridge_create_book`, `local_lms_bridge_create_quiz`, cùng các hàm ghi danh.
Tệp `db/services.php` ở bước sau phải liệt kê **đủ** danh sách này trong mảng `functions` của service, không chỉ hai hàm tự viết.

- [ ] **Step 3: Viết `version.php`**

Tạo `/d/do-an/moodle-plugin/local_lms_bridge/version.php`:

```php
<?php
defined('MOODLE_INTERNAL') || die();

$plugin->component = 'local_lms_bridge';
// Moodle chi doc lai dinh nghia service khi so phien ban tang len.
$plugin->version   = 2026092300;
// Moodle 5.2, xem moodle/public/version.php.
$plugin->requires  = 2026042000;
$plugin->maturity  = MATURITY_STABLE;
$plugin->release   = '1.0.0';
```

- [ ] **Step 4: Viết `db/services.php`**

Tạo `/d/do-an/moodle-plugin/local_lms_bridge/db/services.php`. Mảng `functions` của service phải chứa **đủ** danh sách in ra ở Step 2:

```php
<?php
defined('MOODLE_INTERNAL') || die();

$functions = [
    'local_lms_bridge_create_book' => [
        'classname'   => 'local_lms_bridge\external\create_book',
        'methodname'  => 'execute',
        'description' => 'Tao mot Book gom cac chuong ngan trong mot khoa hoc.',
        'type'        => 'write',
        'capabilities' => 'moodle/course:manageactivities',
    ],
    'local_lms_bridge_create_quiz' => [
        'classname'   => 'local_lms_bridge\external\create_quiz',
        'methodname'  => 'execute',
        'description' => 'Tao mot Quiz gom cac cau hoi nhieu lua chon.',
        'type'        => 'write',
        'capabilities' => 'mod/quiz:addinstance',
    ],
];

$services = [
    'LMS Bridge service' => [
        'functions' => [
            'local_lms_bridge_create_book',
            'local_lms_bridge_create_quiz',
            'core_course_create_courses',
            'core_course_get_courses',
            'core_course_search_courses',
            'core_course_get_contents',
            'enrol_manual_enrol_users',
            'core_enrol_get_enrolled_users',
        ],
        'restrictedusers' => 0,
        'enabled'         => 1,
        'shortname'       => 'lms_bridge',
        'downloadfiles'   => 0,
        'uploadfiles'     => 0,
    ],
];
```

Nếu Step 2 in ra hàm nào chưa có trong mảng trên thì thêm vào, vì test so khớp hai chiều.

- [ ] **Step 5: Viết `lang/en/local_lms_bridge.php`**

Tạo `/d/do-an/moodle-plugin/local_lms_bridge/lang/en/local_lms_bridge.php`:

```php
<?php
defined('MOODLE_INTERNAL') || die();

$string['pluginname'] = 'LMS Bridge';
$string['privacy:metadata'] = 'The LMS Bridge plugin does not store any personal data.';
```

- [ ] **Step 6: Viết `classes/external/create_book.php`**

Tạo `/d/do-an/moodle-plugin/local_lms_bridge/classes/external/create_book.php`:

```php
<?php
namespace local_lms_bridge\external;

defined('MOODLE_INTERNAL') || die();

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;

/**
 * mod_book chi co ham web service dang doc, khong tao duoc hoat dong moi,
 * nen ham nay goi thang add_moduleinfo roi chen chuong vao bang book_chapters.
 */
class create_book extends external_api {

    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'courseid' => new external_value(PARAM_INT, 'Ma khoa hoc'),
            'name'     => new external_value(PARAM_TEXT, 'Ten cua Book'),
            'chapters' => new external_multiple_structure(
                new external_single_structure([
                    'title'      => new external_value(PARAM_TEXT, 'Tieu de chuong'),
                    'content'    => new external_value(PARAM_RAW, 'Noi dung chuong, dang HTML'),
                    // So nguyen chu khong phai bool: Moodle doc truong bieu mau nay
                    // duoi dang so va se tu choi chuoi "True".
                    'subchapter' => new external_value(PARAM_INT, '1 neu la chuong con, nguoc lai 0'),
                ])
            ),
            'sectionnum' => new external_value(PARAM_INT, 'Muc trong khoa hoc', VALUE_DEFAULT, 0),
        ]);
    }

    public static function execute_returns(): external_value {
        return new external_value(PARAM_INT, 'Ma course module cua Book vua tao');
    }

    public static function execute(int $courseid, string $name, array $chapters, int $sectionnum = 0): int {
        global $CFG, $DB;
        require_once($CFG->dirroot . '/course/modlib.php');

        $params = self::validate_parameters(self::execute_parameters(), [
            'courseid' => $courseid, 'name' => $name,
            'chapters' => $chapters, 'sectionnum' => $sectionnum,
        ]);

        $course = get_course($params['courseid']);
        $context = \context_course::instance($course->id);
        self::validate_context($context);
        require_capability('moodle/course:manageactivities', $context);

        $module = $DB->get_record('modules', ['name' => 'book'], '*', MUST_EXIST);

        $data = new \stdClass();
        $data->course = $course->id;
        $data->module = $module->id;
        $data->modulename = 'book';
        $data->section = $params['sectionnum'];
        $data->visible = 1;
        $data->name = $params['name'];
        $data->intro = '';
        $data->introformat = FORMAT_HTML;
        $data->numbering = 1;
        $data->navstyle = 1;
        $data->customtitles = 0;

        $moduleinfo = add_moduleinfo($data, $course);

        $pagenum = 1;
        foreach ($params['chapters'] as $chapter) {
            $row = new \stdClass();
            $row->bookid = $moduleinfo->instance;
            $row->pagenum = $pagenum++;
            $row->subchapter = $chapter['subchapter'];
            $row->title = $chapter['title'];
            $row->content = $chapter['content'];
            $row->contentformat = FORMAT_HTML;
            $row->hidden = 0;
            $row->timecreated = time();
            $row->timemodified = time();
            $row->importsrc = '';
            $DB->insert_record('book_chapters', $row);
        }

        return (int) $moduleinfo->coursemodule;
    }
}
```

- [ ] **Step 7: Viết `classes/external/create_quiz.php`**

Tạo `/d/do-an/moodle-plugin/local_lms_bridge/classes/external/create_quiz.php`:

```php
<?php
namespace local_lms_bridge\external;

defined('MOODLE_INTERNAL') || die();

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;

/**
 * Tao quiz roi them cau hoi nhieu lua chon. mod_quiz khong co ham web service
 * dang ghi, nen phai dung add_moduleinfo cong voi API ngan hang cau hoi.
 */
class create_quiz extends external_api {

    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'courseid'  => new external_value(PARAM_INT, 'Ma khoa hoc'),
            'name'      => new external_value(PARAM_TEXT, 'Ten cua quiz'),
            'questions' => new external_multiple_structure(
                new external_single_structure([
                    'stem'          => new external_value(PARAM_RAW, 'De bai'),
                    'choices'       => new external_multiple_structure(
                        new external_value(PARAM_RAW, 'Mot phuong an')
                    ),
                    'correct_index' => new external_value(PARAM_INT, 'Vi tri dap an dung, tinh tu 0'),
                    'explanation'   => new external_value(PARAM_RAW, 'Giai thich', VALUE_DEFAULT, ''),
                ])
            ),
            'sectionnum' => new external_value(PARAM_INT, 'Muc trong khoa hoc', VALUE_DEFAULT, 0),
        ]);
    }

    public static function execute_returns(): external_value {
        return new external_value(PARAM_INT, 'Ma course module cua quiz vua tao');
    }

    public static function execute(int $courseid, string $name, array $questions, int $sectionnum = 0): int {
        global $CFG, $DB, $USER;
        require_once($CFG->dirroot . '/course/modlib.php');
        require_once($CFG->dirroot . '/question/engine/bank.php');

        $params = self::validate_parameters(self::execute_parameters(), [
            'courseid' => $courseid, 'name' => $name,
            'questions' => $questions, 'sectionnum' => $sectionnum,
        ]);

        $course = get_course($params['courseid']);
        $context = \context_course::instance($course->id);
        self::validate_context($context);
        require_capability('mod/quiz:addinstance', $context);

        $module = $DB->get_record('modules', ['name' => 'quiz'], '*', MUST_EXIST);

        $data = new \stdClass();
        $data->course = $course->id;
        $data->module = $module->id;
        $data->modulename = 'quiz';
        $data->section = $params['sectionnum'];
        $data->visible = 1;
        $data->name = $params['name'];
        $data->intro = '';
        $data->introformat = FORMAT_HTML;
        $data->grade = 10;
        $data->questionsperpage = 1;
        $data->preferredbehaviour = 'deferredfeedback';

        $moduleinfo = add_moduleinfo($data, $course);
        $quizid = $moduleinfo->instance;

        $category = question_get_default_category($context->id, true);

        foreach ($params['questions'] as $index => $question) {
            $qtypeobj = \question_bank::get_qtype('multichoice');

            $form = new \stdClass();
            $form->category = $category->id . ',' . $context->id;
            $form->name = get_string('pluginname', 'local_lms_bridge') . ' Q' . ($index + 1);
            $form->questiontext = ['text' => $question['stem'], 'format' => FORMAT_HTML];
            $form->generalfeedback = ['text' => $question['explanation'], 'format' => FORMAT_HTML];
            $form->defaultmark = 1;
            $form->penalty = 0;
            $form->single = 1;
            $form->shuffleanswers = 1;
            $form->answernumbering = 'abc';
            $form->qtype = 'multichoice';

            foreach ($question['choices'] as $i => $choice) {
                $form->answer[$i] = ['text' => $choice, 'format' => FORMAT_HTML];
                $form->fraction[$i] = ($i === $question['correct_index']) ? 1.0 : 0.0;
                $form->feedback[$i] = ['text' => '', 'format' => FORMAT_HTML];
            }

            $created = new \stdClass();
            $created->category = $category->id;
            $created->qtype = 'multichoice';
            $created->createdby = $USER->id;
            $created = $qtypeobj->save_question($created, $form);

            quiz_add_quiz_question($created->id, $DB->get_record('quiz', ['id' => $quizid]));
        }

        return (int) $moduleinfo->coursemodule;
    }
}
```

- [ ] **Step 8: Chạy test dẫn đường, giờ phải xanh**

```bash
cd /d/do-an/backend
uv run pytest tests/unit/test_moodle_bridge_service.py -v
```

Kỳ vọng: cả 5 test PASSED. Nếu `test_every_function_the_client_calls_is_allowed_by_the_bridge_service` vẫn đỏ thì thêm hàm còn thiếu vào mảng `functions` trong `db/services.php`.

- [ ] **Step 9: Sửa đường mount Moodle trong compose và gắn plugin vào**

Trong `/d/do-an/devops/docker-compose.yml`, thay phần `volumes` của service `moodle`:

```yaml
    volumes:
      # Thu muc da doi ten tu moodle-src-523 thanh moodle. Apache lay
      # /var/www/html/public lam document root, xem devops/moodle/Dockerfile.
      - ./moodle:/var/www/html
      # Gan thang ma plugin tu kho ma vao, thay vi chep tay nhu truoc, de
      # ban trong kho luon la ban dang chay.
      - ./moodle-plugin/local_lms_bridge:/var/www/html/public/local/lms_bridge
      - moodle_data:/var/www/moodledata
```

- [ ] **Step 10: Dựng lại Moodle và kiểm tra plugin được nhận**

```bash
cd /d/do-an
docker compose up -d moodle
sleep 60
docker exec do-an-moodle ls /var/www/html/public/local/lms_bridge
docker exec do-an-moodle php /var/www/html/public/admin/cli/upgrade.php --non-interactive
```

Kỳ vọng: lệnh `ls` liệt kê `version.php`, `db`, `classes`, `lang`. Lệnh nâng cấp chạy xong không lỗi và có nhắc tới `local_lms_bridge`.

- [ ] **Step 11: Kiểm thử thật đầu cuối, không chỉ test đơn vị**

Đây là tính năng người dùng nhìn thấy nên phải chạy thật.

```bash
cd /d/do-an
docker compose up -d
```

Qua giao diện tại http://localhost:5173: đăng nhập, chọn một khóa học đã liên kết Moodle, mở một module đã sinh, bấm xuất bản sang Moodle. Sau đó mở http://localhost:8081, vào đúng khóa học đó và kiểm tra Book hiện ra đủ các chương "Mục tiêu học tập", "Nội dung", "Slide" kèm chương con cho từng slide, và "Ví dụ", cùng hai quiz Practice và Quiz.

Nếu chưa có module nào thì sinh một module trước từ một tài liệu trong `documents/`.

- [ ] **Step 12: Chạy toàn bộ test và lint**

```bash
cd /d/do-an/backend
uv run ruff check .
uv run pytest
```

Kỳ vọng: ruff sạch và toàn bộ test xanh.

- [ ] **Step 13: Commit**

```bash
cd /d/do-an
git checkout -b fix/restore-moodle-plugin
git add moodle-plugin devops/docker-compose.yml
git commit -q -m "fix(moodle): khôi phục plugin cầu nối và sửa đường mount mã nguồn Moodle

Backend vẫn gọi local_lms_bridge_create_book và local_lms_bridge_create_quiz
nhưng mã plugin đã mất, nên chức năng xuất bản hỏng ở mức mã nguồn. Plugin
được gắn thẳng từ kho mã vào Moodle thay vì chép tay, để bản trong kho luôn
là bản đang chạy. Compose cũng còn trỏ vào moodle-src-523 sau khi thư mục
đã đổi tên thành moodle."
```

---

## Những gì Task 3 phát hiện thêm khi chạy thật

Kế hoạch ban đầu chỉ ghi "viết lại plugin và sửa đường mount".
Khi chạy thật thì lần tái cấu trúc trước đó đã làm hỏng gần như toàn bộ đường triển khai, và những lỗi sau phải sửa cùng nhánh vì chúng chặn việc kiểm thử.

`LLM_GATEWAY_URL` trỏ về `localhost`, mà trong container `localhost` chính là container backend, nên backend không gọi được gateway và không sinh được nội dung.
`MOODLE_BASE_URL` và `BACKEND_URL` mắc đúng lỗi này.

Service `migrate` biến mất khỏi compose, nghĩa là migration không bao giờ chạy khi dựng stack.

`SECRET_KEY` không được truyền cho backend, nên mỗi lần khởi động lại sinh khóa tạm và đăng xuất toàn bộ người dùng.

Cổng của bốn service mở ra mọi giao diện mạng thay vì chỉ loopback.

Build context của ảnh Moodle trỏ vào `devops/moodle` trong khi Dockerfile chép tệp theo đường dẫn tính từ gốc kho mã, nên ảnh không build được.

Tệp `config.php` của Moodle đã mất trong khi cơ sở dữ liệu vẫn còn nguyên 491 bảng.
Không nhánh nào trong entrypoint xử lý được tình huống đó, vì installer đòi một cơ sở dữ liệu trống.
Đã thêm `devops/moodle/restore-config.php` để viết lại cấu hình từ biến môi trường rồi để `upgrade.php` lo phần còn lại.

Mật khẩu của vai trò chủ sở hữu trong volume `do-an_db_data` đã lệch khỏi `devops/.env` sau đợt xoay vòng khóa ngày 2026-09-20, vì Postgres chỉ áp `POSTGRES_PASSWORD` khi khởi tạo một volume trống.
Mọi lệnh `docker exec psql` đều đi qua socket nội bộ với xác thực trust nên không lộ ra điều này; chỉ kết nối qua TCP mới thất bại.

Mã nguồn Moodle tải về là bản 5.2 (Build 20260420), cũ hơn bản 5.2.3+ (Build 20260916) đã ghi trong cơ sở dữ liệu, nên Moodle từ chối khởi động.

## Một thứ trong spec cố tình không nằm ở Nhóm A

Mục 3 của spec liệt kê `backend/scripts/` trong danh sách cần khôi phục, nhưng kế hoạch này không dựng lại nó.

Thư mục đó trước đây chứa các script phục vụ từng việc cụ thể: nhúng lại chunk, xuất gói ẩn danh cho nghiên cứu, và đo chuẩn chunking.
Dựng sẵn một thư mục rỗng hoặc chép lại script cũ mà chưa có nơi dùng thì chỉ là mã chết.
Mỗi script sẽ được viết lại trong chính nhóm cần tới nó: script xuất gói ẩn danh thuộc Nhóm D, script đo lại reranker thuộc Nhóm E.

## Sau khi xong Nhóm A

Kiểm tra lại toàn bộ trước khi lập kế hoạch cho Nhóm B:

```bash
cd /d/do-an/backend && uv run pytest && uv run ruff check .
cd /d/do-an/frontend && npm run test && npm run build
```

Cả hai phải xanh, vì đó là đúng những gì CI sẽ chạy.

Bộ test chạy trên máy cần container `ci_test_db` (ảnh pgvector, cổng 55432, user postgres, database `microlearning`, đã bật extension `vector`).
Container này từng bị xóa nên bộ test treo khi cố kết nối.
Luôn dùng `127.0.0.1` chứ không phải `localhost` trong các biến `DATABASE_URL`, vì Docker Desktop nuốt `::1` và mỗi kết nối treo hàng phút.
