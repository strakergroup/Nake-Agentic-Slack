import datetime
from typing import List, Literal, TypeAlias

from pydantic import BaseModel
from sqlalchemy import (
    DateTime,
    Enum,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ObjMMemberCreditTransactions(Base):
    """ObjMMemberCreditTransactions.

    Table: `obj_m_member_credit_transactions`
    """

    __tablename__ = "obj_m_member_credit_transactions"

    CreditType: TypeAlias = Literal["ai_token", "mt_token"]
    TransactionType: TypeAlias = Literal["purchase", "spend", "custom"]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(50), unique=True)
    client_uuid: Mapped[str] = mapped_column(String(50))
    group_uuid: Mapped[str] = mapped_column(String(50))
    organization_uuid: Mapped[str] = mapped_column(String(50))
    amount: Mapped[int] = mapped_column(Integer, default=0)
    credit_type: Mapped[str] = mapped_column(Enum("ai_token", "mt_token"))
    transaction_type: Mapped[str] = mapped_column(Enum("purchase", "spend", "custom"))
    description: Mapped[str] = mapped_column(String(255))
    stripe_payment_intent: Mapped[str] = mapped_column(String(100))
    stripe_checkout_session: Mapped[str] = mapped_column(String(100))
    app_source: Mapped[str] = mapped_column(String(100), default="languagecloud")
    service: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    modified_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class MtTranslationExtraData(BaseModel):
    """Extra data for MT translation requests."""

    client_id: str
    service_language_mapping: dict[str, List[str]]
    source_language: str
    organization_uuid: str
    team_id: str
    channel_id: str
    text_length: int
    usage_type: str
    source_text: str | None = None
    # Response method fields
    response_url: str | None = None
    thread_ts: str | None = None
    is_edit: bool = False
    slack_user_id: str | None = None
    display_format: str | None = None
    message_ts: str | None = None
    glossary_identifier: str | None = None
