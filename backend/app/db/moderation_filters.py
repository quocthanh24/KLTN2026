"""Fail-closed ORM visibility across existing chat/doc/AI/worker access paths.

Only server-owned administration queries opt out. Never an HTTP query/body option.
"""
from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria


@event.listens_for(Session,'do_orm_execute')
def restrict_moderated(execute_state):
    if not execute_state.is_select or execute_state.execution_options.get('include_moderated',False):
        return
    from app.models import Workspace, ChatMessage, LearningDocument
    execute_state.statement=execute_state.statement.options(
        with_loader_criteria(Workspace,lambda cls:cls.disabled_at.is_(None),include_aliases=True),
        with_loader_criteria(ChatMessage,lambda cls:cls.moderated_at.is_(None),include_aliases=True),
        with_loader_criteria(LearningDocument,lambda cls:cls.moderated_at.is_(None),include_aliases=True))
