"""Предметные модели Workwear и декларативные ограничения Django.

Триггеры и составные FK заданы миграциями; они не заменены
методом save(). Запись через ORM напрямую не подменяет сервисную авторизацию.
Карта полей и полный каталог ограничений: docs/architecture.md."""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Organization(models.Model):
    """Постоянная организация; имя для интерфейса отделено от имени источника."""
    name = models.CharField("Наименование", max_length=255)
    external_id = models.CharField("Идентификатор 1С (orgExtId)", max_length=255,
                                   unique=True, null=True, blank=True)
    source_name = models.CharField("Последнее название из 1С", max_length=255,
                                   blank=True, editable=False)
    requests_enabled = models.BooleanField("Разрешено создавать заявки", default=False)

    class Meta:
        verbose_name = "Организация"
        verbose_name_plural = "Организации"
        ordering = ["name", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(name__regex=r"\S"), name="ww_org_name_ck"),
            models.CheckConstraint(
                condition=Q(external_id__isnull=True) | (
                    Q(external_id__regex=r"^[^\s]+$") & Q(external_id=models.functions.Lower("external_id"))
                ), name="ww_org_external_id_ck",
            ),
        ]

    def clean(self):
        super().clean()
        self.external_id = self.external_id.strip().casefold() if self.external_id else None

    def __str__(self):
        return self.name


class EmployeeImport(models.Model):
    """Принятая тройка справочников: хеши, дата приёма и подтверждённые метаданные источника."""
    employees_sha256 = models.CharField(max_length=64)
    positions_sha256 = models.CharField(max_length=64)
    units_sha256 = models.CharField(max_length=64)
    accepted_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )
    source_row_count = models.PositiveIntegerField()
    employee_count = models.PositiveIntegerField()
    # Поля задаёт источник; accepted_at не является датой выгрузки.
    source_exported_at = models.DateTimeField(null=True, blank=True)
    coverage_start = models.DateField(null=True, blank=True)
    coverage_end = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Принятая выгрузка сотрудников"
        verbose_name_plural = "Принятые выгрузки сотрудников"
        ordering = ["-accepted_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "employees_sha256",
                    "positions_sha256",
                    "units_sha256",
                ],
                name="ww_import_hash_triplet_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    Q(employees_sha256__regex=r"^[0-9a-f]{64}$")
                    & Q(positions_sha256__regex=r"^[0-9a-f]{64}$")
                    & Q(units_sha256__regex=r"^[0-9a-f]{64}$")
                ),
                name="ww_import_hash_format_ck",
            ),
            models.CheckConstraint(
                condition=Q(
                    source_row_count__gte=F("employee_count")
                ),
                name="ww_import_counts_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(coverage_start__isnull=True, coverage_end__isnull=True)
                    | Q(coverage_start__isnull=False, coverage_end__isnull=False,
                        coverage_start__lte=F("coverage_end"))
                ),
                name="ww_import_coverage_pair_ck",
            ),
        ]


class Employee(models.Model):
    """Постоянный GUID сотрудника и актуальные атрибуты справочника.

    Разрешена коррекция атрибутов источника, но id, GUID и организация неизменны.
    Исторические поля RequestEmployee от обновления Employee не меняются."""
    employee_guid = models.UUIDField(unique=True)
    personnel_number = models.CharField(max_length=64)
    full_name = models.CharField(max_length=255)
    position_name = models.CharField(
        max_length=255,
        blank=True,
    )
    department_name = models.CharField(
        max_length=255,
        blank=True,
    )
    organization_name = models.CharField(max_length=255)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT,
                                     related_name="employees", verbose_name="Организация")
    last_seen_import = models.ForeignKey(
        EmployeeImport,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="last_seen_employees",
    )

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"
        ordering = ["full_name", "pk"]
        indexes = [
            models.Index(
                fields=["organization", "full_name"],
                name="ww_emp_org_name_idx",
            ),
            models.Index(
                fields=["organization", "personnel_number"],
                name="ww_emp_org_number_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["id", "organization"],
                name="ww_emp_id_org_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(personnel_number="")
                    & ~Q(full_name="")
                    & ~Q(organization_name="")
                    & ~Q(organization_name="NULL")
                ),
                name="ww_employee_required_ck",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.personnel_number})"


class WorkwearItem(models.Model):
    """Управляемая позиция. Атрибуты каждого назначения фиксируются отдельно."""
    code = models.CharField("Код", max_length=32, unique=True)
    name = models.CharField("Наименование", max_length=120)
    category = models.CharField("Категория", max_length=64)
    validity_days = models.SmallIntegerField("Срок действия, дней",
        validators=[MinValueValidator(1), MaxValueValidator(32767)])
    is_active = models.BooleanField("Активна", default=True)
    sort_order = models.PositiveIntegerField("Порядок отображения", default=0)

    class Meta:
        verbose_name = "Позиция спецодежды"
        verbose_name_plural = "Позиции спецодежды"
        ordering = ["sort_order", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(validity_days__gte=1, validity_days__lte=32767),
                name="ww_item_validity_ck",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(code="")
                    & ~Q(name="")
                    & ~Q(category="")
                ),
                name="ww_item_required_ck",
            ),
        ]

    def __str__(self):
        return self.name


class RequestStatus(models.TextChoices):
    """Допустимые состояния жизненного цикла заявки: draft и closed."""
    DRAFT = "draft", "Черновик"
    CLOSED = "closed", "Завершена"


class Request(models.Model):
    """Заявка: организация, версия и серверные атрибуты завершения.

    Нативное дополнительное permission reopen_request отделено от change_request.
    SQL guard требует начальный draft и защищает переходы/завершённые записи."""
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT,
                                     related_name="requests", verbose_name="Организация")
    # Название на момент создания, независимое от последующих правок справочника.
    organization_name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=12,
        choices=RequestStatus.choices,
        default=RequestStatus.DRAFT,
    )
    version = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="workwear_created_requests",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="workwear_completed_requests",
    )

    class Meta:
        verbose_name = "Заявка на спецодежду"
        verbose_name_plural = "Заявки на спецодежду"
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=[
                    "organization",
                    "status",
                    "-created_at",
                ],
                name="ww_req_org_status_dt_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["id", "organization"],
                name="ww_req_id_org_uniq",
            ),
            models.CheckConstraint(
                condition=Q(status__in=RequestStatus.values),
                name="ww_req_status_ck",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="ww_req_version_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=RequestStatus.DRAFT,
                        completed_at__isnull=True,
                        completed_by__isnull=True,
                    )
                    | Q(
                        status=RequestStatus.CLOSED,
                        completed_at__isnull=False,
                        completed_by__isnull=False,
                    )
                ),
                name="ww_req_completion_ck",
            ),
        ]
        permissions = [
            (
                "reopen_request",
                "Возврат завершённой заявки в работу",
            ),
        ]


class RequestEmployee(models.Model):
    """Участник заявки с кадровыми snapshot-полями на момент включения.

    Составные FK связывают организацию заявки, текущего Employee и участника.
    Поздняя коррекция кадрового номера не переписывает исторический снимок."""
    request = models.ForeignKey(
        Request,
        on_delete=models.CASCADE,
        related_name="members",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="workwear_memberships",
    )
    organization_name = models.CharField(max_length=255)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT,
                                     related_name="memberships", verbose_name="Организация")

    snapshot_full_name = models.CharField(
        max_length=255,
        blank=True,
    )
    snapshot_personnel_number = models.CharField(
        max_length=64,
        blank=True,
    )
    snapshot_organization_name = models.CharField(
        max_length=255,
        blank=True,
    )
    snapshot_position_name = models.CharField(
        max_length=255,
        blank=True,
    )
    snapshot_department_name = models.CharField(
        max_length=255,
        blank=True,
    )

    class Meta:
        verbose_name = "Сотрудник в заявке"
        verbose_name_plural = "Сотрудники в заявках"
        constraints = [
            models.UniqueConstraint(
                fields=["request", "employee"],
                name="ww_member_request_emp_uniq",
            ),
        ]


class RequestAssignment(models.Model):
    """Уникальная позиция каталога для участника. Закрытые назначения защищены SQL guard."""
    request_employee = models.ForeignKey(
        RequestEmployee,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    item = models.ForeignKey(
        WorkwearItem,
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    # PostgreSQL заполняет снимок при INSERT, включая bulk_create и прямой SQL.
    snapshot_item_code = models.CharField(max_length=32, editable=False)
    snapshot_item_name = models.CharField(max_length=120, editable=False)
    snapshot_item_category = models.CharField(max_length=64, editable=False)
    snapshot_validity_days = models.SmallIntegerField(editable=False)

    class Meta:
        verbose_name = "Назначение спецодежды"
        verbose_name_plural = "Назначения спецодежды"
        constraints = [
            models.CheckConstraint(
                condition=Q(snapshot_validity_days__gte=1, snapshot_validity_days__lte=32767),
                name="ww_assignment_validity_ck",
            ),
            models.CheckConstraint(
                condition=~Q(snapshot_item_code="") & ~Q(snapshot_item_name="") & ~Q(snapshot_item_category=""),
                name="ww_assignment_snapshot_ck",
            ),
            models.UniqueConstraint(
                fields=["request_employee", "item"],
                name="ww_assignment_member_item_uniq",
            ),
        ]


class WorkwearEvent(models.Model):
    # События сохраняются после удаления черновика.
    # Поэтому обязательного FK на Request нет.
    """Неизменяемое событие с независимым request_number и snapshot имени автора.

    Отсутствие FK на Request сохраняет события после удаления черновика.
    PostgreSQL запрещает UPDATE/DELETE/TRUNCATE; обход ORM не снимает защиту."""
    request_number = models.BigIntegerField()
    event_type = models.CharField(max_length=64)
    occurred_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="workwear_events",
    )
    actor_username = models.CharField(max_length=150)
    details = models.JSONField(default=dict)

    class Meta:
        verbose_name = "Событие Workwear"
        verbose_name_plural = "История событий Workwear"
        ordering = ["-occurred_at", "-pk"]
        indexes = [
            models.Index(
                fields=[
                    "request_number",
                    "-occurred_at",
                    "-id",
                ],
                name="ww_event_req_time_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(request_number__gte=1),
                name="ww_event_req_positive_ck",
            ),
            models.CheckConstraint(
                condition=~Q(event_type=""),
                name="ww_event_type_nonempty_ck",
            ),
        ]
