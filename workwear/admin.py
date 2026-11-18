"""Справочники редактируем в Django Admin; заявки изменяем только через сервисы."""
from django.contrib import admin

from .models import Organization, WorkwearItem


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "external_id", "requests_enabled", "source_name")
    list_filter = ("requests_enabled",)
    search_fields = ("name", "external_id", "source_name")
    readonly_fields = ("source_name",)

    def get_readonly_fields(self, request, obj=None):
        # Внешний ID можно заполнить один раз; затем идентичность защищает БД.
        return self.readonly_fields + (("external_id",) if obj and obj.external_id else ())


@admin.register(WorkwearItem)
class WorkwearItemAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "validity_days", "sort_order", "is_active")
    list_filter = ("is_active", "category")
    search_fields = ("code", "name", "category")
    ordering = ("sort_order", "pk")

    def get_readonly_fields(self, request, obj=None):
        return ("code",) if obj else ()
