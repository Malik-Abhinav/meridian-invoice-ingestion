"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-13 14:26:18.331572

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001_initial'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('audit_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('actor_type', sa.String(length=16), nullable=False),
    sa.Column('actor_id', sa.String(length=160), nullable=True),
    sa.Column('action', sa.String(length=120), nullable=False),
    sa.Column('entity_type', sa.String(length=80), nullable=False),
    sa.Column('entity_id', sa.Uuid(), nullable=False),
    sa.Column('before_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("actor_type <> 'staff' OR (actor_id IS NOT NULL AND length(trim(actor_id)) > 0)", name='ck_audit_events_staff_actor'),
    sa.CheckConstraint("actor_type IN ('system', 'staff')", name='ck_audit_events_actor_type'),
    sa.CheckConstraint('length(trim(action)) > 0', name='ck_audit_events_action_not_blank'),
    sa.CheckConstraint('length(trim(entity_type)) > 0', name='ck_audit_events_entity_not_blank'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_events_actor_time', 'audit_events', ['actor_type', 'actor_id', 'created_at'], unique=False)
    op.create_index('ix_audit_events_entity_time', 'audit_events', ['entity_type', 'entity_id', 'created_at'], unique=False)
    op.create_table('invoice_documents',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_uri', sa.String(length=1024), nullable=False),
    sa.Column('content_type', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name='ck_invoice_documents_sha256_hex'),
    sa.CheckConstraint('length(trim(storage_uri)) > 0', name='ck_invoice_documents_uri_not_blank'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sha256')
    )
    op.create_table('locations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=60), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(trim(code)) > 0', name='ck_locations_code_not_blank'),
    sa.CheckConstraint('length(trim(name)) > 0', name='ck_locations_name_not_blank'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('products',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('upc_normalized', sa.String(length=20), nullable=True),
    sa.Column('ndc_normalized', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(trim(name)) > 0', name='ck_products_name_not_blank'),
    sa.CheckConstraint('ndc_normalized IS NULL OR length(trim(ndc_normalized)) > 0', name='ck_products_ndc_not_blank'),
    sa.CheckConstraint('upc_normalized IS NOT NULL OR ndc_normalized IS NOT NULL', name='ck_products_has_identifier'),
    sa.CheckConstraint('upc_normalized IS NULL OR length(trim(upc_normalized)) > 0', name='ck_products_upc_not_blank'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_products_ndc_normalized', 'products', ['ndc_normalized'], unique=True, postgresql_where=sa.text('ndc_normalized IS NOT NULL'))
    op.create_index('uq_products_upc_normalized', 'products', ['upc_normalized'], unique=True, postgresql_where=sa.text('upc_normalized IS NOT NULL'))
    op.create_table('suppliers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(trim(code)) > 0', name='ck_suppliers_code_not_blank'),
    sa.CheckConstraint('length(trim(name)) > 0', name='ck_suppliers_name_not_blank'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('inventory_balances',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('product_id', sa.Uuid(), nullable=False),
    sa.Column('location_id', sa.Uuid(), nullable=False),
    sa.Column('available_quantity', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('available_quantity >= 0', name='ck_inventory_balances_nonnegative'),
    sa.ForeignKeyConstraint(['location_id'], ['locations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_inventory_balances_location_product', 'inventory_balances', ['location_id', 'product_id'], unique=False)
    op.create_index('uq_inventory_balances_product_location', 'inventory_balances', ['product_id', 'location_id'], unique=True)
    op.create_table('invoices',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('supplier_id', sa.Uuid(), nullable=False),
    sa.Column('invoice_number_raw', sa.String(length=120), nullable=True),
    sa.Column('invoice_number_normalized', sa.String(length=120), nullable=True),
    sa.Column('invoice_date', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('subtotal', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('tax', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('total', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('uploaded', 'extracted', 'review_required', 'validated', 'approved', 'posted', 'rejected', 'failed')", name='ck_invoices_status'),
    sa.CheckConstraint('invoice_number_normalized IS NULL OR length(trim(invoice_number_normalized)) > 0', name='ck_invoices_number_not_blank'),
    sa.CheckConstraint('subtotal IS NULL OR subtotal >= 0', name='ck_invoices_subtotal_nonnegative'),
    sa.CheckConstraint('tax IS NULL OR tax >= 0', name='ck_invoices_tax_nonnegative'),
    sa.CheckConstraint('total IS NULL OR total >= 0', name='ck_invoices_total_nonnegative'),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_invoices_status_created', 'invoices', ['status', 'created_at'], unique=False)
    op.create_index('uq_invoices_supplier_number', 'invoices', ['supplier_id', 'invoice_number_normalized'], unique=True, postgresql_where=sa.text('invoice_number_normalized IS NOT NULL'))
    op.create_table('purchase_orders',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('supplier_id', sa.Uuid(), nullable=False),
    sa.Column('order_number', sa.String(length=120), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'open', 'closed', 'cancelled')", name='ck_purchase_orders_status'),
    sa.CheckConstraint('length(trim(order_number)) > 0', name='ck_purchase_orders_number_not_blank'),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_purchase_orders_supplier_status', 'purchase_orders', ['supplier_id', 'status'], unique=False)
    op.create_index('uq_purchase_orders_supplier_number', 'purchase_orders', ['supplier_id', 'order_number'], unique=True)
    op.create_table('invoice_lines',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('invoice_id', sa.Uuid(), nullable=False),
    sa.Column('line_number', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('product_id', sa.Uuid(), nullable=True),
    sa.Column('upc_raw', sa.String(length=80), nullable=True),
    sa.Column('ndc_raw', sa.String(length=80), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('unit_cost', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('line_total', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.CheckConstraint('length(trim(description)) > 0', name='ck_invoice_lines_description_not_blank'),
    sa.CheckConstraint('line_number > 0', name='ck_invoice_lines_number_positive'),
    sa.CheckConstraint('line_total IS NULL OR line_total >= 0', name='ck_invoice_lines_total_nonnegative'),
    sa.CheckConstraint('quantity IS NULL OR quantity > 0', name='ck_invoice_lines_quantity_positive'),
    sa.CheckConstraint('unit_cost IS NULL OR unit_cost >= 0', name='ck_invoice_lines_unit_cost_nonnegative'),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_invoice_lines_product_id', 'invoice_lines', ['product_id'], unique=False)
    op.create_index('uq_invoice_lines_invoice_number', 'invoice_lines', ['invoice_id', 'line_number'], unique=True)
    op.create_table('invoice_upload_attempts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('invoice_id', sa.Uuid(), nullable=True),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('identity_status', sa.String(length=24), server_default='pending', nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("identity_status <> 'linked' OR invoice_id IS NOT NULL", name='ck_upload_attempts_linked_invoice'),
    sa.CheckConstraint("identity_status IN ('pending', 'linked', 'duplicate', 'conflict', 'missing_number')", name='ck_upload_attempts_identity_status'),
    sa.ForeignKeyConstraint(['document_id'], ['invoice_documents.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_upload_attempts_document_uploaded', 'invoice_upload_attempts', ['document_id', 'uploaded_at'], unique=False)
    op.create_index('ix_upload_attempts_invoice_uploaded', 'invoice_upload_attempts', ['invoice_id', 'uploaded_at'], unique=False)
    op.create_table('purchase_order_lines',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('purchase_order_id', sa.Uuid(), nullable=False),
    sa.Column('line_number', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Uuid(), nullable=False),
    sa.Column('ordered_quantity', sa.Integer(), nullable=False),
    sa.Column('expected_unit_cost', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.CheckConstraint('expected_unit_cost >= 0', name='ck_purchase_order_lines_cost_nonnegative'),
    sa.CheckConstraint('line_number > 0', name='ck_purchase_order_lines_number_positive'),
    sa.CheckConstraint('ordered_quantity > 0', name='ck_purchase_order_lines_quantity_positive'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_orders.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_purchase_order_lines_product_id', 'purchase_order_lines', ['product_id'], unique=False)
    op.create_index('uq_purchase_order_lines_order_number', 'purchase_order_lines', ['purchase_order_id', 'line_number'], unique=True)
    op.create_table('inventory_movements',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('product_id', sa.Uuid(), nullable=False),
    sa.Column('location_id', sa.Uuid(), nullable=False),
    sa.Column('movement_type', sa.String(length=16), nullable=False),
    sa.Column('quantity_delta', sa.Integer(), nullable=False),
    sa.Column('unit_cost', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('posting_key', sa.String(length=160), nullable=False),
    sa.Column('invoice_line_id', sa.Uuid(), nullable=True),
    sa.Column('purchase_order_line_id', sa.Uuid(), nullable=True),
    sa.Column('reverses_movement_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(movement_type = 'receipt' AND quantity_delta > 0 AND invoice_line_id IS NOT NULL AND purchase_order_line_id IS NOT NULL AND reverses_movement_id IS NULL) OR (movement_type = 'adjustment' AND invoice_line_id IS NULL AND purchase_order_line_id IS NULL AND reverses_movement_id IS NULL) OR (movement_type = 'reversal' AND invoice_line_id IS NULL AND purchase_order_line_id IS NULL AND reverses_movement_id IS NOT NULL)", name='ck_inventory_movements_sources'),
    sa.CheckConstraint("movement_type IN ('receipt', 'adjustment', 'reversal')", name='ck_inventory_movements_type'),
    sa.CheckConstraint('length(trim(posting_key)) > 0', name='ck_inventory_movements_key_not_blank'),
    sa.CheckConstraint('quantity_delta <> 0', name='ck_inventory_movements_nonzero'),
    sa.CheckConstraint('reverses_movement_id IS NULL OR reverses_movement_id <> id', name='ck_inventory_movements_no_self_reversal'),
    sa.CheckConstraint('unit_cost IS NULL OR unit_cost >= 0', name='ck_inventory_movements_cost_nonnegative'),
    sa.ForeignKeyConstraint(['invoice_line_id'], ['invoice_lines.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['location_id'], ['locations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['purchase_order_line_id'], ['purchase_order_lines.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reverses_movement_id'], ['inventory_movements.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('posting_key')
    )
    op.create_index('ix_inventory_movements_po_line', 'inventory_movements', ['purchase_order_line_id'], unique=False)
    op.create_index('ix_inventory_movements_product_location_time', 'inventory_movements', ['product_id', 'location_id', 'created_at'], unique=False)
    op.create_index('uq_inventory_movements_receipt_invoice_line', 'inventory_movements', ['invoice_line_id'], unique=True, postgresql_where=sa.text("movement_type = 'receipt'"))
    op.create_index('uq_inventory_movements_reversal_target', 'inventory_movements', ['reverses_movement_id'], unique=True, postgresql_where=sa.text("movement_type = 'reversal'"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_inventory_movements_reversal_target', table_name='inventory_movements', postgresql_where=sa.text("movement_type = 'reversal'"))
    op.drop_index('uq_inventory_movements_receipt_invoice_line', table_name='inventory_movements', postgresql_where=sa.text("movement_type = 'receipt'"))
    op.drop_index('ix_inventory_movements_product_location_time', table_name='inventory_movements')
    op.drop_index('ix_inventory_movements_po_line', table_name='inventory_movements')
    op.drop_table('inventory_movements')
    op.drop_index('uq_purchase_order_lines_order_number', table_name='purchase_order_lines')
    op.drop_index('ix_purchase_order_lines_product_id', table_name='purchase_order_lines')
    op.drop_table('purchase_order_lines')
    op.drop_index('ix_upload_attempts_invoice_uploaded', table_name='invoice_upload_attempts')
    op.drop_index('ix_upload_attempts_document_uploaded', table_name='invoice_upload_attempts')
    op.drop_table('invoice_upload_attempts')
    op.drop_index('uq_invoice_lines_invoice_number', table_name='invoice_lines')
    op.drop_index('ix_invoice_lines_product_id', table_name='invoice_lines')
    op.drop_table('invoice_lines')
    op.drop_index('uq_purchase_orders_supplier_number', table_name='purchase_orders')
    op.drop_index('ix_purchase_orders_supplier_status', table_name='purchase_orders')
    op.drop_table('purchase_orders')
    op.drop_index('uq_invoices_supplier_number', table_name='invoices', postgresql_where=sa.text('invoice_number_normalized IS NOT NULL'))
    op.drop_index('ix_invoices_status_created', table_name='invoices')
    op.drop_table('invoices')
    op.drop_index('uq_inventory_balances_product_location', table_name='inventory_balances')
    op.drop_index('ix_inventory_balances_location_product', table_name='inventory_balances')
    op.drop_table('inventory_balances')
    op.drop_table('suppliers')
    op.drop_index('uq_products_upc_normalized', table_name='products', postgresql_where=sa.text('upc_normalized IS NOT NULL'))
    op.drop_index('uq_products_ndc_normalized', table_name='products', postgresql_where=sa.text('ndc_normalized IS NOT NULL'))
    op.drop_table('products')
    op.drop_table('locations')
    op.drop_table('invoice_documents')
    op.drop_index('ix_audit_events_entity_time', table_name='audit_events')
    op.drop_index('ix_audit_events_actor_time', table_name='audit_events')
    op.drop_table('audit_events')
