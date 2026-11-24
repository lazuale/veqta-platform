"""Обязательные объекты Workwear для проверки установленной схемы."""

EVENT_TABLE = "workwear_workwearevent"
IMPORT_TABLE = "workwear_employeeimport"

FOREIGN_KEYS = (
    ("workwear_requestemployee", "ww_member_req_org_fk",
     "FOREIGN KEY (request_id, organization_id) REFERENCES public.workwear_request(id, organization_id) ON UPDATE RESTRICT"),
    ("workwear_requestemployee", "ww_member_emp_org_fk",
     "FOREIGN KEY (employee_id, organization_id) REFERENCES public.workwear_employee(id, organization_id) ON UPDATE RESTRICT"),
)

# tgtype задаёт момент, события и уровень срабатывания триггера.
TRIGGERS = (
    ("workwear_employee", "ww_employee_identity_bu", "ww_guard_employee_identity", 19),
    ("workwear_request", "ww_request_identity_bu", "ww_guard_request_identity", 19),
    (EVENT_TABLE, "ww_event_no_update_delete", "ww_reject_event_change", 27),
    (EVENT_TABLE, "ww_event_no_truncate", "ww_reject_event_change", 34),
    ("workwear_requestemployee", "ww_member_write_biud", "ww_guard_member_write", 31),
    ("workwear_requestassignment", "ww_assignment_write_biud", "ww_guard_assignment_write", 31),
    ("workwear_request", "ww_request_lifecycle_bud", "ww_guard_request_lifecycle", 27),
    ("workwear_request", "ww_request_no_truncate", "ww_reject_structure_truncate", 34),
    ("workwear_requestemployee", "ww_member_no_truncate", "ww_reject_structure_truncate", 34),
    ("workwear_requestassignment", "ww_assignment_no_truncate", "ww_reject_structure_truncate", 34),
    ("workwear_request", "ww_request_initial_bi", "ww_guard_request_insert", 7),
    ("workwear_organization", "ww_organization_identity_bu", "ww_guard_organization_identity", 19),
    ("workwear_workwearitem", "ww_item_identity_bu", "ww_guard_item_identity", 19),
    (IMPORT_TABLE, "ww_import_no_update_delete", "ww_reject_import_change", 27),
    (IMPORT_TABLE, "ww_import_no_truncate", "ww_reject_import_change", 34),
)

# SHA-256 тела функций обнаруживает замену защитной логики при сохранении триггера.
FUNCTIONS = {
    "ww_guard_employee_identity": "5e245ba9d0c077743e3c9e1be61cc3967e783f89044493eb6802783bdeba7638",
    "ww_guard_request_identity": "abb022091237827805b3ad52e202300e201acf73795df581c5dc4a009f30197a",
    "ww_reject_event_change": "62a1b1469b47717007c7212cd973028d0aa9fcb706c9e7bf7a8b8f4e035e3b63",
    "ww_guard_member_write": "ab752fc077daca53d492f3c04721d201549b49db11505ee97f6cb03c6240ca2c",
    "ww_guard_assignment_write": "a9ae4314fb08b4441290c2f7678a60cddc5c093309fd759b1b257a4051bec470",
    "ww_guard_request_lifecycle": "b8c03294c41eb91fa4c76e17fa57e17a8fa2e25af186bd42acba98a63c0f1c55",
    "ww_reject_structure_truncate": "5f4f7cdf387f191ed5d765b0b91082b729419699af2806a9be96b5206346e708",
    "ww_guard_request_insert": "704d42401e581acb4912fbe1c21fc8ea2bd78b7bb2499e0140fddcfbd6dfc322",
    "ww_guard_organization_identity": "5717dda1b1c2d1d4efe3dc13bc2719c3d41a8df91c1f26b3abb2ccff3528b05c",
    "ww_guard_item_identity": "cad4ddfc34c29aa01e482ddc0b531423ee35e64a33cb69bc41e9510846c35816",
    "ww_reject_import_change": "e665e1714c4feb5f3ca67ac352375840c7d85baf91e6480f1c7176fd6813e1ee",
}

CONSTRAINTS = (
    ("workwear_employeeimport", "ww_import_hash_triplet_uniq", "u",
     ("employees_sha256", "positions_sha256", "units_sha256")),
    ("workwear_employeeimport", "ww_import_hash_format_ck", "c",
     ("employees_sha256", "positions_sha256", "units_sha256", "^[0-9a-f]{64}$")),
    ("workwear_employeeimport", "ww_import_counts_ck", "c",
     ("source_row_count", "employee_count", ">=")),
    ("workwear_request", "ww_req_status_ck", "c", ("status", "draft", "closed")),
    ("workwear_request", "ww_req_version_ck", "c", ("version", ">= 1")),
    ("workwear_request", "ww_req_completion_ck", "c",
     ("status", "completed_at", "completed_by", "draft", "closed", "IS NULL", "IS NOT NULL")),
    ("workwear_requestemployee", "ww_member_snapshot_ck", "c",
     ("snapshot_full_name", "snapshot_personnel_number", "snapshot_organization_name")),
    ("workwear_requestassignment", "ww_assignment_validity_ck", "c",
     ("snapshot_validity_days", ">= 1", "<= 32767")),
    ("workwear_requestassignment", "ww_assignment_snapshot_ck", "c",
     ("snapshot_item_code", "snapshot_item_name", "snapshot_item_category")),
)
