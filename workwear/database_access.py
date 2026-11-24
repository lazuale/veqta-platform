"""Обязательные объекты Workwear для проверки установленной схемы."""

EVENT_TABLE = "workwear_workwearevent"

# tgtype кодирует момент, события и уровень срабатывания триггера.
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
)
