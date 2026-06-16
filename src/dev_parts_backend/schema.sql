PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dev_part_master (
    master_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT,
    source_sheet TEXT,
    base_model TEXT,
    base_grade TEXT,
    new_model TEXT,
    new_grade TEXT,
    event TEXT,
    region TEXT,
    project_name TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dev_part_detail (
    detail_id INTEGER PRIMARY KEY AUTOINCREMENT,
    master_id INTEGER NOT NULL,
    line_order INTEGER,
    row_no INTEGER,
    bom_level TEXT,
    bom_depth INTEGER,
    parent_detail_id INTEGER,
    part_type TEXT,
    base_part_no TEXT,
    new_part_no TEXT,
    normalized_new_part_no TEXT,
    part_name TEXT,
    base_qty TEXT,
    new_qty TEXT,
    normalized_new_qty TEXT,
    changing_point TEXT,
    changing_reason TEXT,
    supplier TEXT,
    classification TEXT,
    mold_dev_modify INTEGER,
    inhouse_prod INTEGER,
    part_approval_test INTEGER,
    raw_json TEXT,
    changing_text TEXT,
    changing_embedding TEXT,
    changing_reason_embedding TEXT,
    changing_point_embedding TEXT,
    part_name_embedding TEXT,
    combined_embedding TEXT,
    FOREIGN KEY (master_id) REFERENCES dev_part_master(master_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_detail_id) REFERENCES dev_part_detail(detail_id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_dev_part_detail_master_line
    ON dev_part_detail(master_id, line_order);

CREATE INDEX IF NOT EXISTS idx_dev_part_detail_base_part_no
    ON dev_part_detail(base_part_no);

CREATE INDEX IF NOT EXISTS idx_dev_part_detail_new_part_no
    ON dev_part_detail(new_part_no);

CREATE INDEX IF NOT EXISTS idx_dev_part_detail_normalized_new_part_no
    ON dev_part_detail(normalized_new_part_no);

CREATE INDEX IF NOT EXISTS idx_dev_part_detail_parent
    ON dev_part_detail(parent_detail_id);

CREATE VIRTUAL TABLE IF NOT EXISTS dev_part_detail_fts USING fts5(
    detail_id UNINDEXED,
    part_name,
    base_part_no,
    new_part_no,
    changing_point,
    changing_reason,
    supplier,
    classification
);
