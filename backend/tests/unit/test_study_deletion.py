from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.deletion import check_study_references, commit_deletion


def test_referenced_deletion_is_refused():
    db = Mock()
    db.scalar.return_value = True
    with pytest.raises(HTTPException) as error:
        check_study_references(db, [])
    assert error.value.status_code == 409
    db.commit.assert_not_called()


def test_unreferenced_deletion_commits():
    db = Mock()
    db.scalar.return_value = False
    check_study_references(db, [])
    commit_deletion(db)
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


def test_constraint_race_rolls_back_and_returns_conflict():
    db = Mock()
    original = Exception("retained reference")
    original.sqlstate = "23503"
    db.commit.side_effect = IntegrityError("DELETE", {}, original)
    with pytest.raises(HTTPException) as error:
        commit_deletion(db)
    assert error.value.status_code == 409
    db.rollback.assert_called_once()


def test_unrelated_integrity_error_rolls_back_and_propagates():
    db = Mock()
    original = Exception("other constraint")
    original.sqlstate = "23505"
    db.commit.side_effect = IntegrityError("DELETE", {}, original)
    with pytest.raises(IntegrityError):
        commit_deletion(db)
    db.rollback.assert_called_once()
