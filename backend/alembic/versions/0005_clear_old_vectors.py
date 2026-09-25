"""Xoá vector của mọi chunk khi model nhúng đổi từ Gemini sang Qwen3-Embedding chạy tại chỗ.

Revision ID: 0005_clear_old_vectors
Revises: 0004_question_position
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_clear_old_vectors"
down_revision: str | Sequence[str] | None = "0004_question_position"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Vector của hai model khác nhau nằm trong hai không gian khác nhau: cùng 768 số nên database vẫn
# nhận và vẫn so, nhưng câu hỏi nhúng bằng model mới so với chunk nhúng bằng model cũ thì thứ hạng
# là ngẫu nhiên, và không có lỗi nào báo. Xoá đi thì chunk chỉ còn tìm được bằng BM25 cho tới khi
# scripts/reembed_chunks.py nhúng lại, thay vì âm thầm trả sai. Nội dung chunk giữ nguyên nên
# không phải đọc lại tài liệu.


def upgrade() -> None:
    op.execute("UPDATE chunks SET embedding = NULL WHERE embedding IS NOT NULL")


def downgrade() -> None:
    # Quay về model cũ cũng là đổi model: vector hiện có thuộc không gian của Qwen3.
    op.execute("UPDATE chunks SET embedding = NULL WHERE embedding IS NOT NULL")
