# Nhóm B - Dọn phình - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bỏ phần chính sách mật khẩu phức tạp cùng vai trò chết, và hoà tan thư mục `services/` thành các thư mục mỗi cái một mối quan tâm.

**Architecture:** Hai nhánh độc lập, mỗi nhánh một merge request. Task 1 xoá mã và test; Task 2 chỉ dời tệp và sửa import, không đổi hành vi.

**Tech Stack:** Python 3.12 với uv, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16, React với Vite và TypeScript.

**Spec:** `docs/specs/2026-09-23-kien-truc-gd2-design.md`

## Global Constraints

Không dùng dấu gạch dài trong bất kỳ văn bản nào, dùng dấu gạch ngắn.

Thông điệp commit theo Conventional Commits, không thêm dòng co-author.

Test viết trước phần cài đặt, theo hợp đồng của tính năng chứ không theo mã đã viết.

Trước khi mở merge request: `uv run ruff check .` sạch và toàn bộ `uv run pytest` xanh, không chỉ riêng test của nhánh đó. Mốc hiện tại là 581 test xanh.

Comment giải thích lý do, không mô tả lại mã.

Bộ test cần container `ci_test_db` đang chạy (ảnh `pgvector/pgvector:pg16`, cổng 55432, user và mật khẩu `postgres`, database `microlearning`). Nếu bộ test treo thay vì báo lỗi thì kiểm tra `docker ps` trước, vì container này đã bị mất ba lần.

Luôn dùng `127.0.0.1` chứ không phải `localhost` trong các biến `DATABASE_URL`.

Không bao giờ in giá trị biến môi trường chứa khoá hay mật khẩu, chỉ kiểm tra theo tên và độ dài.

## Ba chỗ kế hoạch này cố tình làm khác spec

**Giữ `test_compose_topology.py`.** Spec xếp nó vào nhóm test hạ tầng nên xoá.
Trong Nhóm A nó bắt được bốn hồi quy thật mà không test nào khác thấy, trong đó có lỗi `LLM_GATEWAY_URL` trỏ về `localhost` làm backend không gọi được gateway.
Một test đã chứng minh giá trị như vậy thì không phải là phình.

**Không tạo `app/core/`.** Spec ghi gộp `config`, `db`, `llm gateway`, `job queue` vào một thư mục `core/`.
Nhưng `app/db.py` đang được 50 tệp import và `app/config.py` được 22 tệp, nên dời chúng là chạm vào khoảng 60 tệp để đổi lấy rất ít; `config.py` và `db.py` nằm ở gốc package vốn là quy ước bình thường của FastAPI.
Quan trọng hơn, một thư mục tên `core` gần như chắc chắn sẽ trở thành ngăn kéo tạp mới, đúng thứ nhánh này đang dọn.
Thay vào đó `services/` được tách theo đúng một mối quan tâm mỗi thư mục: `llm/`, `jobs/`, `chat/`, `moodle/`.

**Không tạo `backend/docker/`.** Spec ghi gom `Dockerfile`, entrypoint và `pgvector.sql` vào đó.
Tệp `pgvector.sql` đã bị bỏ trong Nhóm A vì migration tự tạo extension, nên chỉ còn hai tệp, mà `Dockerfile` nằm ở gốc build context là quy ước phổ biến.

---

## Task 1: Bỏ chính sách mật khẩu phức tạp và vai trò chết

Nhánh `refactor/trim-auth`.

Giữ lại hai thứ trong `password_policy.py` vì chúng không phải chính sách mà là ràng buộc kỹ thuật: độ dài tối thiểu, và giới hạn 72 byte của bcrypt.
Bỏ giới hạn 72 byte sẽ khiến bcrypt âm thầm cắt đuôi mật khẩu, tức là hai mật khẩu khác nhau lại đăng nhập được vào cùng một tài khoản.

**Files:**
- Delete: `backend/app/auth/common_passwords.py`
- Modify: `backend/app/auth/password_policy.py`
- Modify: `backend/tests/test_registration_and_passwords.py`
- Modify: `backend/app/models/models.py:28-30`
- Create: `backend/alembic/versions/0002_drop_student_role.py`
- Create: `backend/tests/integration/test_role_enum_migration.py`
- Delete: `backend/tests/test_production_mode.py`
- Delete: `backend/tests/test_config_secrets.py`

**Interfaces:**
- Consumes: `check_password(password: str, *, email: str, name: str) -> str | None` từ `app/auth/password_policy.py`, giữ nguyên chữ ký
- Produces: `check_password` chỉ còn hai lý do từ chối; `UserRole` chỉ còn giá trị `teacher`; revision alembic `0002_drop_student_role` với `down_revision = "0001_initial_schema"`

- [ ] **Step 1: Sửa test trước, bỏ ba test của các quy tắc sắp xoá**

Trong `backend/tests/test_registration_and_passwords.py`, xoá ba hàm test và các danh sách tham số của chúng:

```bash
cd /d/do-an/backend
grep -n "test_a_common_password_is_refused\|test_a_password_with_almost_no_variety_is_refused\|test_a_password_equal_to_the_email_the_local_part_or_the_name_is_refused" tests/test_registration_and_passwords.py
```

Xoá trọn ba hàm đó cùng decorator `@pytest.mark.parametrize` ngay phía trên mỗi hàm.

Rồi thêm một test mới khẳng định điều ngược lại, để hành vi mới được ghi nhận chứ không chỉ là mất test:

```python
@pytest.mark.parametrize(
    "password",
    ["password123", "aaaaaaaaaa", "matkhau123"],
)
def test_a_long_enough_password_is_accepted_however_simple_it_looks(password):
    # Chính sách giờ chỉ còn độ dài: các quy tắc đoán "mật khẩu yếu" từng bị bỏ đi
    # vì chúng đẩy người dùng tới những mật khẩu kiểu Password1!, vốn nằm trong
    # mọi danh sách của kẻ tấn công.
    email = f"don-gian-{uuid.uuid4().hex[:8]}@example.com"

    response = client.post(
        "/auth/register", json={"email": email, "name": "Người dùng", "password": password}
    )

    assert response.status_code == 201
```

- [ ] **Step 2: Chạy test để xác nhận nó fail**

```bash
cd /d/do-an/backend
uv run pytest tests/test_registration_and_passwords.py::test_a_long_enough_password_is_accepted_however_simple_it_looks -v
```

Kỳ vọng: FAIL với mã trạng thái 400, vì `password123` còn nằm trong danh sách mật khẩu phổ biến.

- [ ] **Step 3: Rút gọn `password_policy.py`**

Thay toàn bộ `backend/app/auth/password_policy.py` bằng:

```python
from app.auth.security import MAX_PASSWORD_BYTES

MIN_PASSWORD_LENGTH = 8


def check_password(password: str, *, email: str, name: str) -> str | None:
    """Vì sao mật khẩu bị từ chối, viết cho chính người đang đặt nó, hoặc None khi chấp nhận.

    Chỉ còn độ dài, không còn quy tắc đoán mật khẩu yếu: những quy tắc đó đẩy người dùng
    tới các mật khẩu kiểu Password1! vốn nằm trong mọi danh sách của kẻ tấn công, trong khi
    một câu dài bình thường lại khó đoán hơn nhiều.

    Giới hạn 72 byte không phải chính sách mà là ràng buộc của bcrypt: nó chỉ đọc 72 byte
    đầu, nên nếu không chặn ở đây thì hai mật khẩu khác nhau sẽ cùng mở được một tài khoản.
    Hai tham số email và name được giữ lại trong chữ ký để nơi gọi không phải đổi.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự."
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return (
            f"Mật khẩu tối đa {MAX_PASSWORD_BYTES} byte (mỗi chữ tiếng Việt có dấu chiếm 2 đến 3 byte), "
            "hãy chọn mật khẩu ngắn hơn."
        )
    return None
```

- [ ] **Step 4: Xoá danh sách mật khẩu phổ biến**

```bash
cd /d/do-an/backend
rm app/auth/common_passwords.py
grep -rn "common_passwords\|COMMON_PASSWORDS" app/ tests/ --include=*.py
```

Kỳ vọng: lệnh `grep` không in ra gì.

- [ ] **Step 5: Chạy lại nhóm test mật khẩu**

```bash
cd /d/do-an/backend
uv run pytest tests/test_registration_and_passwords.py -v
```

Kỳ vọng: tất cả PASS, trong đó có các test về 72 byte vẫn còn nguyên.

- [ ] **Step 6: Viết test cho migration bỏ vai trò `student` (phải fail trước)**

Tạo `backend/tests/integration/test_role_enum_migration.py`:

```python
"""Vai trò student là mã chết: mọi lần đăng ký đều gán cứng teacher, và ứng dụng này
chỉ dành cho giảng viên vì học viên học trên Moodle.

Bỏ một giá trị khỏi enum của PostgreSQL không làm trực tiếp được, phải dựng lại kiểu,
nên việc đó đáng có test riêng."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def db_sau_migration():
    """Một cơ sở dữ liệu trắng đã chạy toàn bộ migration."""
    from app.config import settings

    ten_db = "test_role_enum"
    goc = settings.database_url.rsplit("/", 1)[0]
    quan_tri = create_engine(f"{goc}/postgres", isolation_level="AUTOCOMMIT")
    with quan_tri.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {ten_db}"))
        conn.execute(text(f"CREATE DATABASE {ten_db}"))

    url = f"{goc}/{ten_db}"
    ket_qua = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, check=False, capture_output=True, text=True, timeout=300,
        env={**os.environ, "DATABASE_URL": url},
    )
    if ket_qua.returncode != 0:
        pytest.fail(f"alembic upgrade head thất bại:\n{ket_qua.stderr[-3000:]}")
    engine = create_engine(url)

    yield engine

    engine.dispose()
    with quan_tri.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {ten_db}"))
    quan_tri.dispose()


def test_enum_vai_tro_chi_con_teacher(db_sau_migration):
    with db_sau_migration.connect() as conn:
        gia_tri = {
            r[0] for r in conn.execute(text(
                "select enumlabel from pg_enum e "
                "join pg_type t on t.oid = e.enumtypid where t.typname = 'userrole'"
            ))
        }

    assert gia_tri == {"teacher"}


def test_cot_role_van_dung_kieu_userrole(db_sau_migration):
    with db_sau_migration.connect() as conn:
        kieu = conn.execute(text(
            "select format_type(atttypid, atttypmod) from pg_attribute "
            "where attrelid = 'users'::regclass and attname = 'role'"
        )).scalar()

    assert kieu == "userrole"
```

- [ ] **Step 7: Chạy test để xác nhận nó fail**

```bash
cd /d/do-an/backend
uv run pytest tests/integration/test_role_enum_migration.py -v
```

Kỳ vọng: `test_enum_vai_tro_chi_con_teacher` FAIL vì tập giá trị là `{"teacher", "student"}`.

- [ ] **Step 8: Bỏ `student` khỏi mô hình**

Trong `backend/app/models/models.py`, sửa:

```python
class UserRole(str, enum.Enum):
    teacher = "teacher"
    student = "student"
```

thành:

```python
class UserRole(str, enum.Enum):
    # Chỉ có giảng viên đăng nhập ứng dụng này; học viên học trên Moodle sau khi
    # giảng viên xuất bản, nên không có tài khoản nào ở đây.
    teacher = "teacher"
```

- [ ] **Step 9: Viết migration dựng lại kiểu enum**

Tạo `backend/alembic/versions/0002_drop_student_role.py`:

```python
"""Bỏ giá trị student khỏi enum userrole.

PostgreSQL không có lệnh bỏ một giá trị khỏi enum, nên phải dựng lại cả kiểu.

Revision ID: 0002_drop_student_role
Revises: 0001_initial_schema
Create Date: 2026-09-23

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_drop_student_role"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Dừng lại thay vì tự đổi dữ liệu: nếu thật sự có tài khoản student thì đó là
    # điều bất ngờ, và đổi thầm họ thành giảng viên là trao quyền cho người lạ.
    con_lai = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM users WHERE role = 'student'")
    ).scalar()
    if con_lai:
        raise RuntimeError(
            f"Còn {con_lai} tài khoản mang vai trò student. Hãy xử lý chúng trước khi bỏ vai trò này."
        )

    op.execute("ALTER TYPE userrole RENAME TO userrole_cu")
    op.execute("CREATE TYPE userrole AS ENUM ('teacher')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::text::userrole"
    )
    op.execute("DROP TYPE userrole_cu")


def downgrade() -> None:
    op.execute("ALTER TYPE userrole RENAME TO userrole_cu")
    op.execute("CREATE TYPE userrole AS ENUM ('teacher', 'student')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::text::userrole"
    )
    op.execute("DROP TYPE userrole_cu")
```

- [ ] **Step 10: Chạy test migration, giờ phải xanh**

```bash
cd /d/do-an/backend
uv run pytest tests/integration/test_role_enum_migration.py -v
```

Kỳ vọng: cả hai test PASS.

- [ ] **Step 11: Áp migration lên cơ sở dữ liệu test và cơ sở dữ liệu thật**

```bash
cd /d/do-an/backend
uv run alembic upgrade head
```

```bash
cd /d/do-an
docker compose run --rm migrate
```

Kỳ vọng: cả hai lệnh chạy xong không lỗi và in `Running upgrade 0001_initial_schema -> 0002_drop_student_role`.

- [ ] **Step 12: Xoá hai tệp test hạ tầng**

```bash
cd /d/do-an/backend
rm tests/test_production_mode.py tests/test_config_secrets.py
```

Không xoá `tests/unit/test_compose_topology.py`: xem mục "Ba chỗ kế hoạch này cố tình làm khác spec" ở trên.

- [ ] **Step 13: Chạy toàn bộ test và lint**

```bash
cd /d/do-an/backend
uv run ruff check .
uv run pytest -q
```

Kỳ vọng: ruff sạch, toàn bộ test xanh. Số test giảm so với 581 vì đã xoá bớt.

- [ ] **Step 14: Commit**

```bash
cd /d/do-an
git checkout -b refactor/trim-auth
git add -A backend
git commit -q -m "refactor(auth): rút chính sách mật khẩu về độ dài và bỏ vai trò student

Danh sách mật khẩu phổ biến, quy tắc đếm ký tự khác nhau và quy tắc không trùng
email hay họ tên bị bỏ: chúng đẩy người dùng tới những mật khẩu kiểu Password1!
vốn nằm trong mọi danh sách của kẻ tấn công, trong khi một câu dài bình thường
lại khó đoán hơn. Giới hạn 72 byte được giữ vì đó là ràng buộc của bcrypt chứ
không phải chính sách: thiếu nó thì hai mật khẩu khác nhau cùng mở được một
tài khoản.

Vai trò student là mã chết, mọi lần đăng ký đều gán cứng teacher, và ứng dụng
chỉ dành cho giảng viên. PostgreSQL không bỏ được một giá trị khỏi enum nên
migration dựng lại cả kiểu, và dừng lại nếu còn tài khoản mang vai trò đó.

Hai tệp test hạ tầng bị xoá. test_compose_topology.py được giữ vì trong Nhóm A
nó bắt được bốn hồi quy thật mà không test nào khác thấy."
```

---

## Task 2: Hoà tan thư mục services

Nhánh `refactor/folder-structure`. Chỉ dời tệp và sửa import, không đổi một dòng hành vi nào.

Thư mục `app/services/` đang chứa bốn thứ khác loại: `chat.py` là một tính năng, `moodle_client.py` là tích hợp bên ngoài, còn `llm.py`, `job_queue.py` và `maintenance.py` là hạ tầng của hai mảng khác nhau.

**Files:**
- Create: `backend/app/llm/__init__.py`, `backend/app/llm/gateway.py` (từ `app/services/llm.py`)
- Create: `backend/app/jobs/__init__.py`, `backend/app/jobs/queue.py` (từ `app/services/job_queue.py`), `backend/app/jobs/maintenance.py`
- Create: `backend/app/chat/__init__.py`, `backend/app/chat/service.py` (từ `app/services/chat.py`)
- Create: `backend/app/moodle/__init__.py`, `backend/app/moodle/client.py` (từ `app/services/moodle_client.py`)
- Delete: `backend/app/services/`
- Create: `frontend/src/api/` gồm `client.ts`, `auth.ts`, `courses.ts`, `moodle.ts`, `documents.ts`, `modules.ts`, `jobs.ts`, `index.ts`
- Delete: `frontend/src/api.ts`

**Interfaces:**
- Consumes: kết quả Task 1 không bắt buộc, hai nhánh độc lập nhau
- Produces: `app.llm.gateway` phơi bày `complete`, `complete_structured`, `transcribe_audio`; `app.jobs.queue` phơi bày `enqueue_job`, `claim_next_job`, `execute_job_step`, `handle_job_error`, `is_terminal_error`, `start_worker`, `stop_worker`; `app.jobs.maintenance` phơi bày `purge_old_records`; `app.chat.service` phơi bày `answer_question`, `condense_query`, `HISTORY_TURNS`; `app.moodle.client` phơi bày `MoodleError` và các hàm gọi web service; `frontend/src/api/index.ts` tái xuất mọi thứ mà `src/api.ts` từng xuất, nên không tệp nào phải đổi câu lệnh import

- [ ] **Step 1: Ghi lại mốc để so sánh sau khi dời**

```bash
cd /d/do-an/backend
uv run pytest -q 2>&1 | tail -2
```

Ghi lại con số này. Sau khi dời xong, số test xanh phải y hệt, vì đây chỉ là dời tệp.

- [ ] **Step 2: Dời năm tệp bằng `git mv`**

```bash
cd /d/do-an/backend
mkdir -p app/llm app/jobs app/chat app/moodle
touch app/llm/__init__.py app/jobs/__init__.py app/chat/__init__.py app/moodle/__init__.py
git mv app/services/llm.py app/llm/gateway.py
git mv app/services/job_queue.py app/jobs/queue.py
git mv app/services/maintenance.py app/jobs/maintenance.py
git mv app/services/chat.py app/chat/service.py
git mv app/services/moodle_client.py app/moodle/client.py
git rm -q app/services/__init__.py
```

- [ ] **Step 3: Sửa mọi câu lệnh import**

Tạo `backend/doi_import_tmp.py` rồi chạy nó:

```python
"""Đổi đường dẫn import sau khi dời tệp ra khỏi app/services. Chạy một lần rồi xoá."""
import pathlib

DOI = {
    "app.services.llm": "app.llm.gateway",
    "app.services.job_queue": "app.jobs.queue",
    "app.services.maintenance": "app.jobs.maintenance",
    "app.services.chat": "app.chat.service",
    "app.services.moodle_client": "app.moodle.client",
    "from app.services import moodle_client": "from app.moodle import client as moodle_client",
    "from app.services import llm": "from app.llm import gateway as llm",
    "from app.services import job_queue": "from app.jobs import queue as job_queue",
}

so_tep = 0
for thu_muc in ("app", "tests", "alembic"):
    for tep in pathlib.Path(thu_muc).rglob("*.py"):
        if "__pycache__" in tep.parts:
            continue
        cu = tep.read_text(encoding="utf-8")
        moi = cu
        for tu, sang in DOI.items():
            moi = moi.replace(tu, sang)
        if moi != cu:
            tep.write_text(moi, encoding="utf-8", newline="\n")
            so_tep += 1
print(f"da sua {so_tep} tep")
```

```bash
cd /d/do-an/backend
uv run python doi_import_tmp.py
rm doi_import_tmp.py
grep -rn "app.services" app/ tests/ alembic/ --include=*.py
```

Kỳ vọng: lệnh `grep` cuối không in ra gì.

- [ ] **Step 4: Sửa thứ tự import rồi kiểm tra ứng dụng nạp được**

```bash
cd /d/do-an/backend
uv run ruff check . --fix
uv run ruff check .
uv run python -c "import app.main; print('nap duoc')"
```

Kỳ vọng: ruff báo "All checks passed!" và in `nap duoc`.

- [ ] **Step 5: Chạy toàn bộ test, phải bằng đúng mốc ở Step 1**

```bash
cd /d/do-an/backend
uv run pytest -q 2>&1 | tail -2
```

Kỳ vọng: số test xanh y hệt Step 1. Nếu lệch thì có tệp bị sót, quay lại Step 3.

- [ ] **Step 6: Tách `frontend/src/api.ts` thành thư mục `src/api/`**

Đọc tệp hiện tại để biết ranh giới:

```bash
cd /d/do-an/frontend
grep -n "^export" src/api.ts
```

Tạo `src/api/` rồi chia theo miền, giữ nguyên từng thân hàm, chỉ đổi chỗ đặt:

- `client.ts`: `api` (thể hiện axios), phần đọc ghi token trong localStorage, bộ chặn làm mới token, `navigation`, `apiErrorMessage`, và kiểu `UserInfo`
- `auth.ts`: `login`, `register`, `logout`
- `courses.ts`: `listCourses`, `createCourse`, `deleteCourse`, kiểu `Course`
- `moodle.ts`: `searchMoodleCourses`, `linkMoodleCourse`, `createMoodleCourseFor`, `unlinkMoodleCourse`, `getMoodleSections`, `publishToMoodle`, kiểu `MoodleCourseSearchResult`, `MoodleSection`, `PublishToMoodleResult`
- `documents.ts`: `listDocuments`, `deleteDocument`, `uploadDocument`, `listSections`, kiểu `DocumentKind`, `DocumentInfo`, `SectionInfo`
- `modules.ts`: `listModules`, `generateModule`, `generateModulesFromSections`, `getModuleJobStatus`, `pollJobStatus`, `getModule`, `updateModuleTitle`, `updateComponentContent`, kiểu `ModuleSummary`, `QuestionInfo`, `ModuleComponentInfo`, `ModuleDetail`, `JobStatusInfo`
- `jobs.ts`: `listFailedJobs`, kiểu `FailedJobInfo`

Mỗi tệp miền bắt đầu bằng `import { api } from './client'`.

- [ ] **Step 7: Viết `src/api/index.ts` tái xuất mọi thứ**

Tạo `frontend/src/api/index.ts`:

```typescript
// Tái xuất để 17 tệp đang viết `from "../api"` không phải đổi gì: chúng vẫn
// nhập được như cũ, còn mã mới thì nhập thẳng từ tệp của đúng miền cần dùng.
export * from './client'
export * from './auth'
export * from './courses'
export * from './moodle'
export * from './documents'
export * from './modules'
export * from './jobs'
```

```bash
cd /d/do-an/frontend
rm src/api.ts
```

- [ ] **Step 8: Kiểm tra frontend biên dịch và test xanh**

```bash
cd /d/do-an/frontend
npm run test
npm run build
```

Kỳ vọng: cả hai lệnh chạy xong không lỗi. `src/api.test.ts` vẫn nhập `from './api'` và giờ trỏ vào `src/api/index.ts`.

- [ ] **Step 9: Kiểm thử thật bằng trình duyệt**

Đây là thay đổi chạm vào mọi lời gọi API của giao diện, nên phải xem tận mắt chứ không chỉ chạy test.

```bash
cd /d/do-an
docker compose up -d
```

Mở http://localhost:5173, đăng nhập, mở danh sách khoá học, mở một khoá học, mở một module.
Kỳ vọng: không trang nào trắng, không lỗi đỏ trong console của trình duyệt.

- [ ] **Step 10: Dọn ba tài liệu nghiên cứu khỏi thư mục gốc**

Ba tệp này đã không còn được git theo dõi, nên dời chúng chỉ là thao tác trên đĩa và không ảnh hưởng kho mã.
Đưa chúng vào đúng chỗ mà spec đã xếp, để thư mục gốc chỉ còn những thứ thật sự thuộc về nó.

```bash
cd /d/do-an
mkdir -p docs/references
mv "Applying AI Tools to Design Microlearning Materials.md" docs/references/
mv "Bloom Taxonomy.md" docs/references/
mv "The MIND Model A Microlearning AI Integrated Instructional Design for Enhanced Learning Outcomes.md" docs/references/
ls *.md
```

Kỳ vọng: thư mục gốc chỉ còn `GenerativeAI.md` và `README.md`.

Không sửa `.gitignore`: ba dòng chặn theo tên tệp trong đó trở thành thừa sau bước này, nhưng tệp đó do người dùng tự quản và ba dòng thừa thì vô hại.

- [ ] **Step 11: Commit**

```bash
cd /d/do-an
git checkout -b refactor/folder-structure
git add -A backend frontend
git commit -q -m "refactor: hoà tan thư mục services và tách api của giao diện theo miền

Thư mục app/services chứa bốn thứ khác loại: chat là một tính năng,
moodle_client là tích hợp bên ngoài, còn llm, job_queue và maintenance là hạ
tầng của hai mảng khác nhau. Sau khi tách thành app/llm, app/jobs, app/chat và
app/moodle thì mỗi thư mục có đúng một lý do tồn tại.

Không tạo app/core như spec ghi: app/db.py đang được 50 tệp import và
app/config.py được 22, nên dời chúng là chạm vào khoảng 60 tệp để đổi lấy rất
ít, và một thư mục tên core gần như chắc chắn sẽ thành ngăn kéo tạp mới.

frontend/src/api.ts dài 392 dòng được tách thành một tệp cho mỗi miền, kèm
index.ts tái xuất để 17 tệp đang nhập từ đó không phải đổi gì.

Chỉ dời tệp và sửa import, không đổi hành vi: số test xanh trước và sau bằng nhau."
```

---

## Sau khi xong Nhóm B

```bash
cd /d/do-an/backend && uv run pytest -q && uv run ruff check .
cd /d/do-an/frontend && npm run test && npm run build
```

Cả hai phải xanh, vì đó là đúng những gì CI chạy.

Gộp trên GitHub theo thứ tự `refactor/trim-auth` rồi `refactor/folder-structure`, vì cả hai đều đụng vào `backend/tests`.
