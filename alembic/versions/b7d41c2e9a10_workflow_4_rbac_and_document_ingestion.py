"""workflow 4: link memberships to roles, add document ingestion tables

Revision ID: b7d41c2e9a10
Revises: fa5baa52dbd8
Create Date: 2026-11-03 21:00:00

Data changes on existing rows:
- Every tenant gets admin, manager, member and viewer roles with their permissions.
- Membership role strings become role_id: owner and admin map to admin, manager and
  viewer keep their name, and anything else maps to member.
- Team memberships that violate tenant isolation are DELETED, because the new composite
  foreign keys cannot be created while they exist: rows whose team is in another tenant,
  and rows whose user is not a member of the tenant.
- Permissions without a role are deleted, because they could never grant anything.

The role seed is frozen here on purpose: a migration must keep producing the same result
even if app/security/permissions.py changes later.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d41c2e9a10"
down_revision: Union[str, None] = "fa5baa52dbd8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE_PERMISSIONS = {
    "admin": (
        ("tenant", "manage"),
        ("team", "manage"),
        ("team", "access_all"),
        ("document", "upload"),
        ("document", "read"),
        ("document", "delete"),
    ),
    "manager": (
        ("team", "manage"),
        ("document", "upload"),
        ("document", "read"),
        ("document", "delete"),
    ),
    "member": (
        ("document", "upload"),
        ("document", "read"),
    ),
    "viewer": (
        ("document", "read"),
    ),
}

ROLE_DESCRIPTIONS = {
    "admin": "Full control of the tenant and every team in it.",
    "manager": "Manages a team and its documents.",
    "member": "Reads and uploads documents.",
    "viewer": "Read-only access to documents.",
}

ROLE_NAME_FROM_LEGACY_STRING = """
    CASE
        WHEN m.role IN ('owner', 'admin') THEN 'admin'
        WHEN m.role IN ('manager', 'viewer') THEN m.role
        ELSE 'member'
    END
"""


def _seed_roles() -> None:
    bind = op.get_bind()
    for name, description in ROLE_DESCRIPTIONS.items():
        bind.execute(
            sa.text(
                "INSERT INTO roles (tenant_id, name, description, created_at) "
                "SELECT t.id, :name, :description, now() FROM tenants t "
                "ON CONFLICT (tenant_id, name) DO NOTHING"
            ),
            {"name": name, "description": description},
        )
    for name, permissions in ROLE_PERMISSIONS.items():
        for resource, action in permissions:
            bind.execute(
                sa.text(
                    "INSERT INTO permissions (tenant_id, role_id, resource, action, created_at) "
                    "SELECT r.tenant_id, r.id, :resource, :action, now() FROM roles r WHERE r.name = :name "
                    "ON CONFLICT ON CONSTRAINT uq_permission_scope_action DO NOTHING"
                ),
                {"name": name, "resource": resource, "action": action},
            )


def _convert_membership_role(table: str, foreign_key: str) -> None:
    op.add_column(table, sa.Column("role_id", sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {table} m SET role_id = r.id FROM roles r "
        f"WHERE r.tenant_id = m.tenant_id AND r.name = {ROLE_NAME_FROM_LEGACY_STRING}"
    )
    op.alter_column(table, "role_id", nullable=False)
    op.drop_column(table, "role")
    op.create_index(op.f(f"ix_{table}_role_id"), table, ["role_id"], unique=False)
    op.create_foreign_key(foreign_key, table, "roles", ["role_id", "tenant_id"], ["id", "tenant_id"])


def _restore_membership_role(table: str, foreign_key: str) -> None:
    op.add_column(table, sa.Column("role", sa.String(length=50), nullable=True))
    op.execute(f"UPDATE {table} m SET role = r.name FROM roles r WHERE r.id = m.role_id")
    op.alter_column(table, "role", nullable=False)
    op.drop_constraint(foreign_key, table, type_="foreignkey")
    op.drop_index(op.f(f"ix_{table}_role_id"), table_name=table)
    op.drop_column(table, "role_id")


def upgrade() -> None:
    op.create_unique_constraint("uq_team_id_tenant", "teams", ["id", "tenant_id"])
    op.create_unique_constraint("uq_role_id_tenant", "roles", ["id", "tenant_id"])

    op.execute("DELETE FROM permissions WHERE role_id IS NULL")
    op.drop_constraint("permissions_role_id_fkey", "permissions", type_="foreignkey")
    op.alter_column("permissions", "role_id", existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key(
        "fk_permission_role_tenant", "permissions", "roles",
        ["role_id", "tenant_id"], ["id", "tenant_id"], ondelete="CASCADE",
    )

    _seed_roles()
    _convert_membership_role("user_tenant_memberships", "fk_user_tenant_membership_role_tenant")

    op.execute(
        "DELETE FROM user_team_memberships m USING teams t "
        "WHERE t.id = m.team_id AND t.tenant_id <> m.tenant_id"
    )
    op.execute(
        "DELETE FROM user_team_memberships m WHERE NOT EXISTS ("
        "SELECT 1 FROM user_tenant_memberships tm "
        "WHERE tm.user_id = m.user_id AND tm.tenant_id = m.tenant_id)"
    )
    _convert_membership_role("user_team_memberships", "fk_user_team_membership_role_tenant")

    op.drop_constraint("user_team_memberships_team_id_fkey", "user_team_memberships", type_="foreignkey")
    op.create_foreign_key(
        "fk_user_team_membership_team_tenant", "user_team_memberships", "teams",
        ["team_id", "tenant_id"], ["id", "tenant_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_user_team_membership_tenant_membership", "user_team_memberships", "user_tenant_memberships",
        ["user_id", "tenant_id"], ["user_id", "tenant_id"], ondelete="CASCADE",
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("extracted_text_hash", sa.String(length=64), nullable=True),
        sa.Column("file_extension", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_of_document_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["team_id", "tenant_id"], ["teams.id", "teams.tenant_id"],
            ondelete="CASCADE", name="fk_document_team_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "team_id", "stored_filename", name="uq_document_tenant_team_stored_filename"),
        sa.UniqueConstraint("tenant_id", "team_id", "file_hash", name="uq_document_tenant_team_file_hash"),
        sa.UniqueConstraint("id", "tenant_id", "team_id", name="uq_document_id_tenant_team"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')", name="ck_document_status"),
        sa.CheckConstraint("file_size > 0", name="ck_document_file_size_positive"),
    )
    op.create_foreign_key(
        "fk_document_duplicate_same_team", "documents", "documents",
        ["duplicate_of_document_id", "tenant_id", "team_id"], ["id", "tenant_id", "team_id"],
    )
    op.create_index(op.f("ix_documents_id"), "documents", ["id"], unique=False)
    op.create_index(op.f("ix_documents_tenant_id"), "documents", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_documents_team_id"), "documents", ["team_id"], unique=False)
    op.create_index(op.f("ix_documents_uploaded_by_user_id"), "documents", ["uploaded_by_user_id"], unique=False)
    op.create_index("ix_documents_tenant_team_status", "documents", ["tenant_id", "team_id", "status"], unique=False)
    op.create_index("ix_documents_tenant_team_text_hash", "documents", ["tenant_id", "team_id", "extracted_text_hash"], unique=False)

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("chunk_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id", "team_id"],
            ["documents.id", "documents.tenant_id", "documents.team_id"],
            ondelete="CASCADE", name="fk_chunk_document_tenant_team",
        ),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunk_document_index"),
    )
    op.create_index(op.f("ix_document_chunks_id"), "document_chunks", ["id"], unique=False)
    op.create_index("ix_document_chunks_tenant_team", "document_chunks", ["tenant_id", "team_id"], unique=False)

    op.create_table(
        "document_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id", "team_id"],
            ["documents.id", "documents.tenant_id", "documents.team_id"],
            ondelete="CASCADE", name="fk_job_document_tenant_team",
        ),
        sa.CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')", name="ck_document_job_status"),
    )
    op.create_index(op.f("ix_document_jobs_id"), "document_jobs", ["id"], unique=False)
    op.create_index(op.f("ix_document_jobs_document_id"), "document_jobs", ["document_id"], unique=False)
    op.create_index(op.f("ix_document_jobs_created_by_user_id"), "document_jobs", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_document_jobs_celery_task_id"), "document_jobs", ["celery_task_id"], unique=False)
    op.create_index("ix_document_jobs_tenant_team_status", "document_jobs", ["tenant_id", "team_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_table("document_jobs")
    op.drop_table("document_chunks")
    op.drop_table("documents")

    op.drop_constraint("fk_user_team_membership_tenant_membership", "user_team_memberships", type_="foreignkey")
    op.drop_constraint("fk_user_team_membership_team_tenant", "user_team_memberships", type_="foreignkey")
    op.create_foreign_key(
        "user_team_memberships_team_id_fkey", "user_team_memberships", "teams",
        ["team_id"], ["id"], ondelete="CASCADE",
    )
    _restore_membership_role("user_team_memberships", "fk_user_team_membership_role_tenant")
    _restore_membership_role("user_tenant_memberships", "fk_user_tenant_membership_role_tenant")

    op.drop_constraint("fk_permission_role_tenant", "permissions", type_="foreignkey")
    op.alter_column("permissions", "role_id", existing_type=sa.Integer(), nullable=True)
    op.create_foreign_key("permissions_role_id_fkey", "permissions", "roles", ["role_id"], ["id"], ondelete="CASCADE")

    op.drop_constraint("uq_role_id_tenant", "roles", type_="unique")
    op.drop_constraint("uq_team_id_tenant", "teams", type_="unique")
    # Seeded roles and permissions are kept: they are harmless to the previous schema.
