from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    memberships: Mapped[list["UserTenantMembership"]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
        overlaps="users,tenants",
    )
    users: Mapped[list["User"]] = relationship(
        "User",
        secondary="user_tenant_memberships",
        back_populates="tenants",
        overlaps="memberships",
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="tenant", overlaps="owner"
    )
    document_chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="tenant")
    embeddings: Mapped[list["Embedding"]] = relationship(back_populates="tenant")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="tenant")
    messages: Mapped[list["Message"]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    memberships: Mapped[list["UserTenantMembership"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        overlaps="tenants,users",
    )
    tenants: Mapped[list[Tenant]] = relationship(
        "Tenant",
        secondary="user_tenant_memberships",
        back_populates="users",
        overlaps="memberships",
    )
    documents: Mapped[list["Document"]] = relationship(
        "Document",
        back_populates="owner",
        foreign_keys="[Document.owner_user_id]",
        overlaps="owner",
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user",
        foreign_keys="[Conversation.user_id]",
        overlaps="user",
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="user",
        foreign_keys="[Message.user_id]",
        overlaps="user",
    )


class UserTenantMembership(Base):
    __tablename__ = "user_tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_user_tenant_memberships_tenant_user"),
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant_memberships_user_tenant"),
        Index("ix_user_tenant_memberships_tenant_user", "tenant_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="memberships", overlaps="users,tenants")
    user: Mapped[User] = relationship(back_populates="memberships", overlaps="users,tenants")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "storage_key", name="uq_documents_tenant_storage_key"),
        UniqueConstraint("id", "tenant_id", name="uq_documents_id_tenant"),
        Index("ix_documents_tenant_created_at", "tenant_id", "created_at"),
        CheckConstraint(
            "status IN ('uploaded', 'parsed', 'indexed', 'failed')",
            name="ck_documents_status",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "tenant_id"],
            ["user_tenant_memberships.user_id", "user_tenant_memberships.tenant_id"],
            name="fk_documents_owner_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="documents")
    owner: Mapped[User] = relationship(
        "User",
        back_populates="documents",
        foreign_keys="[Document.owner_user_id]",
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="[DocumentChunk.document_id]",
    )
    embeddings: Mapped[list["Embedding"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="[Embedding.document_id]",
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            name="fk_document_chunks_document_tenant",
        ),
        UniqueConstraint("id", "document_id", "tenant_id", name="uq_document_chunks_id_document_tenant"),
        UniqueConstraint("document_id", "chunk_index", "tenant_id", name="uq_document_chunks_document_index_tenant"),
        Index("ix_document_chunks_tenant_document", "tenant_id", "document_id"),
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_chunk_index_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="document_chunks")
    document: Mapped[Document] = relationship(
        back_populates="chunks", foreign_keys="[DocumentChunk.document_id]"
    )
    embedding: Mapped["Embedding"] = relationship(
        back_populates="chunk", uselist=False, cascade="all, delete-orphan", foreign_keys="[Embedding.chunk_id]"
    )


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chunk_id", "document_id", "tenant_id"],
            ["document_chunks.id", "document_chunks.document_id", "document_chunks.tenant_id"],
            name="fk_embeddings_chunk_document_tenant",
        ),
        UniqueConstraint("chunk_id", name="uq_embeddings_chunk"),
        Index("ix_embeddings_tenant_document", "tenant_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    vector: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="embeddings")
    document: Mapped[Document] = relationship(
        back_populates="embeddings", foreign_keys="[Embedding.document_id]"
    )
    chunk: Mapped[DocumentChunk] = relationship(
        back_populates="embedding", foreign_keys="[Embedding.chunk_id]"
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_conversations_id_tenant"),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["user_tenant_memberships.user_id", "user_tenant_memberships.tenant_id"],
            name="fk_conversations_user_tenant",
        ),
        Index("ix_conversations_tenant_user", "tenant_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="conversations")
    user: Mapped[User] = relationship(
        back_populates="conversations",
        foreign_keys="[Conversation.user_id]",
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", foreign_keys="[Message.conversation_id]"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_messages_id_tenant"),
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id"],
            ["conversations.id", "conversations.tenant_id"],
            name="fk_messages_conversation_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["user_tenant_memberships.user_id", "user_tenant_memberships.tenant_id"],
            name="fk_messages_user_tenant",
        ),
        Index("ix_messages_conversation_created_at", "conversation_id", "created_at"),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_messages_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="messages")
    conversation: Mapped[Conversation] = relationship(
        back_populates="messages",
        foreign_keys="[Message.conversation_id]",
    )
    user: Mapped[User] = relationship(
        back_populates="messages",
        foreign_keys="[Message.user_id]",
    )
