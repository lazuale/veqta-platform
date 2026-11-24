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
    "ww_guard_employee_identity": "b0a6535eafda2cea492ec1fda60fc8b038369715759d59989668325a7b2a57a9",
    "ww_guard_request_identity": "77a72bac036642213b245b0670f98beaef9c2716b72505fbb3b2ab9dcc02e0ca",
    "ww_reject_event_change": "7248190d1fef07b1033aaf035dd39754ef6db4e3e09ff4979e26393827b2337c",
    "ww_guard_member_write": "d71a65f10110bbc85347030a8ee7b26212e67fa7e3f94bd5c1bebb1c1024f1f9",
    "ww_guard_assignment_write": "5ba23288832f5bfe07e11907da592be9bc72f5b5a2ce5fc1ddc4ef7f307ed632",
    "ww_guard_request_lifecycle": "534da8f0418043e39bc3cb48a1dce095c6ab96d596f4fa9925ffb3afaecee2ad",
    "ww_reject_structure_truncate": "68bc000a46741723e88abd70e7041134e8ccbb882a0c85fa3a0daaaf480350df",
    "ww_guard_request_insert": "90e05aa630910495815ecc6e2e984e5d8b5860b4d26ca5e13ea72aff024afcb8",
    "ww_guard_organization_identity": "66e131bd35f5260d3ac8f95861fb83fb56fb4a00fcc7026d93351e7ec3d2a92c",
    "ww_guard_item_identity": "b18816f41edac0ad83ff19c8577ea2b772e58bb5334cbf221c6e455d92a5628e",
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
