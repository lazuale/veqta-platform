"""Публичные JSON-маршруты Workwear, включённые в config.urls под /workwear/api/."""
from django.urls import path
from . import api, ui_api

app_name = "workwear"
urlpatterns = [
    path("session/", api.session, name="session"),
    path("requests/", api.request_collection, name="request_collection"),
    path("requests/<int:request_id>/", api.request_detail, name="request_detail"),
    path("requests/<int:request_id>/members/", api.member_add, name="member_add"),
    path("requests/<int:request_id>/members/<int:member_id>/", api.member_remove, name="member_remove"),
    path("requests/<int:request_id>/assignments/", api.assignment_add, name="assignment_add"),
    path("requests/<int:request_id>/assignments/<int:assignment_id>/", api.assignment_remove, name="assignment_remove"),
    path("requests/<int:request_id>/complete/", api.complete, name="complete"),
    path("requests/<int:request_id>/reopen/", api.reopen, name="reopen"),
    path("ui/metadata/", ui_api.metadata, name="ui_metadata"),
    path("ui/requests/", ui_api.registry, name="ui_registry"),
    path("ui/employees/", ui_api.employees, name="ui_employees"),
    path("ui/resolve/", ui_api.resolve, name="ui_resolve"),
    path("ui/requests/<int:request_id>/bulk-add/", ui_api.bulk_add, name="ui_bulk_add"),
    path("ui/requests/<int:request_id>/events/", ui_api.events, name="ui_events"),
    path("ui/requests/<int:request_id>/sync/", ui_api.sync_request, name="ui_sync"),
    path("ui/requests/<int:request_id>/repeat-scan/", ui_api.repeat_scan, name="ui_repeat_scan"),
    path("ui/requests/<int:request_id>/employees/<int:employee_id>/history/", ui_api.employee_history, name="ui_employee_history"),
    path("ui/requests/<int:request_id>/reopen/", ui_api.reopen_with_reason, name="ui_reopen_reason"),
]
