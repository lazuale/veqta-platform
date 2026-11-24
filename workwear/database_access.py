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
    "ww_guard_employee_identity": "7ea1945a01acedbfcad9bc2a47f661b614f98659068272b9c39092f1a4769c17",
    "ww_guard_request_identity": "eca611553d273bcea32179f72de0fa1c3e4e2283946752e0e6a2e3a6aa95068b",
    "ww_reject_event_change": "b15dead6d5c250e4957d692ea52dd4db8b3442303e4de66912a081de70173eee",
    "ww_guard_member_write": "30d87c1f0e883da64fa72867d8cf9cdefb91b07e3096574f39d66ad365609db5",
    "ww_guard_assignment_write": "e155ecdc664221dbbf2107d1974fe3d2a905855bb096084530ed32d683ed9d48",
    "ww_guard_request_lifecycle": "a6ab2d3d8642b66f273605d59e17bc297bec8e2e175f92430eb07f2d6df386b0",
    "ww_reject_structure_truncate": "67260899df7dcfd1bdd417b7edd3431d1a2b9c728f7da65f8ed6314c3420227b",
    "ww_guard_request_insert": "da85233b6ab0e2d784bf2a477e8a97556c3dc360a8bd545dc04a59013071fecd",
    "ww_guard_organization_identity": "3846cc85ed255b7adc8a459ef1fe5751818a6dd826adb5e9b4cf65d44f44cf97",
    "ww_guard_item_identity": "e717ba8e6e2e516479b17f84ab5ff1b4e5850260fc6f73adf48ef20071a64e6d",
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
