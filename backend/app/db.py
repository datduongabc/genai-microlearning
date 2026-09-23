from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# Vai trò chủ sở hữu (siêu người dùng) -- CHỈ cho alembic (DDL), test và script
# dev gọi SessionLocal() trực tiếp. Không module nào trong app/ được dùng nó
# (có test canh): worker và request có vai trò riêng bên dưới, để một lỗ hổng
# trong tiến trình chạy thật không trao cả database.
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Vai trò app_runtime -- CHỈ dùng cho get_db() (request HTTP thật qua
# FastAPI). RLS (migration 881bca16e9bc) áp dụng thật với vai trò này, nên
# mọi truy vấn qua request_engine bị lọc theo course_id/owner_id ở tầng DB,
# không chỉ tầng application -- lớp phòng thủ thứ 2 theo đúng kiến trúc GD2.
request_engine = create_engine(settings.app_runtime_database_url)
RequestSessionLocal = sessionmaker(bind=request_engine, autoflush=False, autocommit=False)


@event.listens_for(RequestSessionLocal, "after_begin")
def _reapply_rls_context(session: Session, transaction, connection) -> None:
    """SET LOCAL chỉ sống trong 1 transaction, hết hiệu lực sau mỗi
    db.commit() -- nhiều route trong repo này commit nhiều lần/request
    (vd tạo Module rồi commit, thêm từng ModuleComponent rồi commit riêng).
    after_begin fire lại ở MỌI transaction mới trong cùng session (kể cả
    transaction ngầm bắt đầu ngay sau 1 commit trước đó), nên set lại tự
    động mỗi lần thay vì chỉ 1 lần lúc get_current_user() -- không thì các
    câu truy vấn SAU commit đầu tiên trong 1 request sẽ mất session var,
    RLS coi như chưa đăng nhập (fail-closed đúng ý, nhưng route sẽ trả về
    rỗng/404 sai thay vì hoạt động đúng)."""
    user_id = session.info.get("rls_user_id")
    if user_id is not None:
        # SET LOCAL không nhận bind parameter (giới hạn cú pháp Postgres) --
        # an toàn nội suy thẳng vì rls_user_id chỉ được set từ user.id thật
        # (uuid.UUID) ở get_current_user(), không phải input người dùng.
        connection.execute(text(f"SET LOCAL app.current_user_id = '{user_id}'"))


# Role app_worker -- the background worker (job_queue.py) that processes the jobs of every
# user. Exempt from row level security because it has to see all of them, and given
# grants for the tables it works on and nothing else (migration a4d7e2c9b6f1).
worker_engine = create_engine(settings.app_worker_database_url)
WorkerSessionLocal = sessionmaker(bind=worker_engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = RequestSessionLocal()
    try:
        yield db
    finally:
        db.close()
