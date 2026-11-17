"""Начальная схема Workwear для новой пустой PostgreSQL.

Модели, индексы и ограничения создаются вместе с составными внешними ключами.
SQL-триггеры защищают идентичность, жизненный цикл и неизменяемую историю.
"""
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployeeImport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('employees_sha256', models.CharField(max_length=64)),
                ('positions_sha256', models.CharField(max_length=64)),
                ('units_sha256', models.CharField(max_length=64)),
                ('accepted_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('source_row_count', models.PositiveIntegerField()),
                ('employee_count', models.PositiveIntegerField()),
                ('source_exported_at', models.DateTimeField(blank=True, null=True)),
                ('coverage_start', models.DateField(blank=True, null=True)),
                ('coverage_end', models.DateField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Принятая выгрузка сотрудников',
                'verbose_name_plural': 'Принятые выгрузки сотрудников',
                'ordering': ['-accepted_at', '-pk'],
                'constraints': [models.UniqueConstraint(fields=('employees_sha256', 'positions_sha256', 'units_sha256'), name='ww_import_hash_triplet_uniq'), models.CheckConstraint(condition=models.Q(('employees_sha256__regex', '^[0-9a-f]{64}$'), ('positions_sha256__regex', '^[0-9a-f]{64}$'), ('units_sha256__regex', '^[0-9a-f]{64}$')), name='ww_import_hash_format_ck'), models.CheckConstraint(condition=models.Q(('source_row_count__gte', models.F('employee_count'))), name='ww_import_counts_ck'), models.CheckConstraint(condition=models.Q(models.Q(('coverage_end__isnull', True), ('coverage_start__isnull', True)), models.Q(('coverage_end__isnull', False), ('coverage_start__isnull', False), ('coverage_start__lte', models.F('coverage_end'))), _connector='OR'), name='ww_import_coverage_pair_ck')],
            },
        ),
        migrations.CreateModel(
            name='Employee',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('employee_guid', models.UUIDField(unique=True)),
                ('personnel_number', models.CharField(max_length=64)),
                ('full_name', models.CharField(max_length=255)),
                ('position_name', models.CharField(blank=True, max_length=255)),
                ('department_name', models.CharField(blank=True, max_length=255)),
                ('organization_name', models.CharField(max_length=255)),
                ('last_seen_import', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='last_seen_employees', to='workwear.employeeimport')),
            ],
            options={
                'verbose_name': 'Сотрудник',
                'verbose_name_plural': 'Сотрудники',
                'ordering': ['full_name', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='Request',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('organization_name', models.CharField(choices=[('АЛМАЗ ООО', 'АЛМАЗ'), ('АВМ ООО', 'АВМ'), ('УПРАВЛЕНИЕ КАРЬЕРАМИ ООО', 'УПРАВЛЕНИЕ КАРЬЕРАМИ')], max_length=255)),
                ('status', models.CharField(choices=[('draft', 'Черновик'), ('closed', 'Завершена')], default='draft', max_length=12)),
                ('version', models.IntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('completed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='workwear_completed_requests', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='workwear_created_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Заявка на спецодежду',
                'verbose_name_plural': 'Заявки на спецодежду',
                'ordering': ['-created_at', '-pk'],
                'permissions': [('reopen_request', 'Возврат завершённой заявки в работу')],
            },
        ),
        migrations.CreateModel(
            name='RequestEmployee',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('organization_name', models.CharField(max_length=255)),
                ('snapshot_full_name', models.CharField(blank=True, max_length=255)),
                ('snapshot_personnel_number', models.CharField(blank=True, max_length=64)),
                ('snapshot_organization_name', models.CharField(blank=True, max_length=255)),
                ('snapshot_position_name', models.CharField(blank=True, max_length=255)),
                ('snapshot_department_name', models.CharField(blank=True, max_length=255)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='workwear_memberships', to='workwear.employee')),
                ('request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='workwear.request')),
            ],
            options={
                'verbose_name': 'Сотрудник в заявке',
                'verbose_name_plural': 'Сотрудники в заявках',
            },
        ),
        migrations.CreateModel(
            name='WorkwearEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_number', models.BigIntegerField()),
                ('event_type', models.CharField(max_length=64)),
                ('occurred_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('actor_username', models.CharField(max_length=150)),
                ('details', models.JSONField(default=dict)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='workwear_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Событие Workwear',
                'verbose_name_plural': 'История событий Workwear',
                'ordering': ['-occurred_at', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='WorkwearItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=32, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('category', models.CharField(max_length=64)),
                ('validity_days', models.SmallIntegerField()),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Позиция спецодежды',
                'verbose_name_plural': 'Позиции спецодежды',
                'ordering': ['sort_order', 'pk'],
                'constraints': [models.CheckConstraint(condition=models.Q(('validity_days__in', [365, 730])), name='ww_item_validity_ck'), models.CheckConstraint(condition=models.Q(models.Q(('code', ''), _negated=True), models.Q(('name', ''), _negated=True), models.Q(('category', ''), _negated=True)), name='ww_item_required_ck')],
            },
        ),
        migrations.CreateModel(
            name='RequestAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignments', to='workwear.requestemployee')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assignments', to='workwear.workwearitem')),
            ],
            options={
                'verbose_name': 'Назначение спецодежды',
                'verbose_name_plural': 'Назначения спецодежды',
            },
        ),
        migrations.AddIndex(
            model_name='employee',
            index=models.Index(fields=['organization_name', 'full_name'], name='ww_emp_org_name_idx'),
        ),
        migrations.AddIndex(
            model_name='employee',
            index=models.Index(fields=['organization_name', 'personnel_number'], name='ww_emp_org_number_idx'),
        ),
        migrations.AddConstraint(
            model_name='employee',
            constraint=models.UniqueConstraint(fields=('id', 'organization_name'), name='ww_emp_id_org_uniq'),
        ),
        migrations.AddConstraint(
            model_name='employee',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('personnel_number', ''), _negated=True), models.Q(('full_name', ''), _negated=True), models.Q(('organization_name', ''), _negated=True), models.Q(('organization_name', 'NULL'), _negated=True)), name='ww_employee_required_ck'),
        ),
        migrations.AddIndex(
            model_name='request',
            index=models.Index(fields=['organization_name', 'status', '-created_at'], name='ww_req_org_status_dt_idx'),
        ),
        migrations.AddConstraint(
            model_name='request',
            constraint=models.UniqueConstraint(fields=('id', 'organization_name'), name='ww_req_id_org_uniq'),
        ),
        migrations.AddConstraint(
            model_name='request',
            constraint=models.CheckConstraint(condition=models.Q(('organization_name__in', ['АЛМАЗ ООО', 'АВМ ООО', 'УПРАВЛЕНИЕ КАРЬЕРАМИ ООО'])), name='ww_req_org_ck'),
        ),
        migrations.AddConstraint(
            model_name='request',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['draft', 'closed'])), name='ww_req_status_ck'),
        ),
        migrations.AddConstraint(
            model_name='request',
            constraint=models.CheckConstraint(condition=models.Q(('version__gte', 1)), name='ww_req_version_ck'),
        ),
        migrations.AddConstraint(
            model_name='request',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('completed_at__isnull', True), ('completed_by__isnull', True), ('status', 'draft')), models.Q(('completed_at__isnull', False), ('completed_by__isnull', False), ('status', 'closed')), _connector='OR'), name='ww_req_completion_ck'),
        ),
        migrations.AddConstraint(
            model_name='requestemployee',
            constraint=models.UniqueConstraint(fields=('request', 'employee'), name='ww_member_request_emp_uniq'),
        ),
        migrations.AddIndex(
            model_name='workwearevent',
            index=models.Index(fields=['request_number', '-occurred_at', '-id'], name='ww_event_req_time_idx'),
        ),
        migrations.AddConstraint(
            model_name='workwearevent',
            constraint=models.CheckConstraint(condition=models.Q(('request_number__gte', 1)), name='ww_event_req_positive_ck'),
        ),
        migrations.AddConstraint(
            model_name='workwearevent',
            constraint=models.CheckConstraint(condition=models.Q(('event_type', ''), _negated=True), name='ww_event_type_nonempty_ck'),
        ),
        migrations.AddConstraint(
            model_name='requestassignment',
            constraint=models.UniqueConstraint(fields=('request_employee', 'item'), name='ww_assignment_member_item_uniq'),
        ),
        migrations.RunSQL(
            sql=r"""
                ALTER TABLE workwear_requestemployee
                ADD CONSTRAINT ww_member_req_org_fk
                FOREIGN KEY (request_id, organization_name)
                REFERENCES workwear_request (id, organization_name)
                ON UPDATE RESTRICT
                ON DELETE NO ACTION
                NOT DEFERRABLE;

                ALTER TABLE workwear_requestemployee
                ADD CONSTRAINT ww_member_emp_org_fk
                FOREIGN KEY (employee_id, organization_name)
                REFERENCES workwear_employee (id, organization_name)
                ON UPDATE RESTRICT
                ON DELETE NO ACTION
                NOT DEFERRABLE;
            """,
            reverse_sql=r"""
                ALTER TABLE workwear_requestemployee
                DROP CONSTRAINT ww_member_emp_org_fk;

                ALTER TABLE workwear_requestemployee
                DROP CONSTRAINT ww_member_req_org_fk;
            """,
        ),
        migrations.RunSQL(
            sql=r"""
                CREATE OR REPLACE FUNCTION ww_guard_employee_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.employee_guid IS DISTINCT FROM OLD.employee_guid
       OR NEW.organization_name IS DISTINCT FROM OLD.organization_name
    THEN
        RAISE EXCEPTION
            'Employee identity is immutable'
            USING
                ERRCODE = '23514',
                CONSTRAINT = 'ww_employee_identity_immutable';
    END IF;

    RETURN NEW;
END;
$$;

                CREATE TRIGGER ww_employee_identity_bu
                BEFORE UPDATE ON workwear_employee
                FOR EACH ROW
                EXECUTE FUNCTION ww_guard_employee_identity();


                CREATE FUNCTION ww_guard_request_identity()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.id IS DISTINCT FROM OLD.id
                       OR NEW.organization_name IS DISTINCT FROM OLD.organization_name
                       OR NEW.created_at IS DISTINCT FROM OLD.created_at
                       OR NEW.created_by_id IS DISTINCT FROM OLD.created_by_id
                    THEN
                        RAISE EXCEPTION
                            'Request identity and origin are immutable'
                            USING
                                ERRCODE = '23514',
                                CONSTRAINT = 'ww_request_identity_immutable';
                    END IF;

                    RETURN NEW;
                END;
                $$;

                CREATE TRIGGER ww_request_identity_bu
                BEFORE UPDATE ON workwear_request
                FOR EACH ROW
                EXECUTE FUNCTION ww_guard_request_identity();


                CREATE FUNCTION ww_reject_event_change()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION
                        'Workwear event history is append-only'
                        USING
                            ERRCODE = '23514',
                            CONSTRAINT = 'ww_event_append_only';

                    RETURN NULL;
                END;
                $$;

                CREATE TRIGGER ww_event_no_update_delete
                BEFORE UPDATE OR DELETE
                ON workwear_workwearevent
                FOR EACH ROW
                EXECUTE FUNCTION ww_reject_event_change();

                CREATE TRIGGER ww_event_no_truncate
                BEFORE TRUNCATE
                ON workwear_workwearevent
                FOR EACH STATEMENT
                EXECUTE FUNCTION ww_reject_event_change();
            """,
            reverse_sql=r"""
                DROP TRIGGER ww_event_no_truncate
                ON workwear_workwearevent;

                DROP TRIGGER ww_event_no_update_delete
                ON workwear_workwearevent;

                DROP FUNCTION ww_reject_event_change();

                DROP TRIGGER ww_request_identity_bu
                ON workwear_request;

                DROP FUNCTION ww_guard_request_identity();

                DROP TRIGGER ww_employee_identity_bu
                ON workwear_employee;

                DROP FUNCTION ww_guard_employee_identity();
            """,
        ),
        migrations.RunSQL(
            sql=r"""
                CREATE FUNCTION ww_guard_member_write()
                RETURNS trigger LANGUAGE plpgsql AS $$
                DECLARE
                    v_request_id bigint;
                    v_status varchar(12);
                BEGIN
                    IF TG_OP = 'INSERT' THEN
                        v_request_id := NEW.request_id;
                    ELSIF TG_OP = 'UPDATE' THEN
                        IF NEW.id IS DISTINCT FROM OLD.id
                           OR NEW.request_id IS DISTINCT FROM OLD.request_id
                           OR NEW.employee_id IS DISTINCT FROM OLD.employee_id
                           OR NEW.organization_name IS DISTINCT FROM OLD.organization_name
                        THEN
                            RAISE EXCEPTION 'Membership identity is immutable'
                                USING ERRCODE = '23514',
                                      CONSTRAINT = 'ww_member_identity_immutable';
                        END IF;
                        v_request_id := OLD.request_id;
                    ELSE
                        v_request_id := OLD.request_id;
                    END IF;

                    SELECT status INTO v_status
                    FROM workwear_request
                    WHERE id = v_request_id
                    FOR UPDATE;

                    IF NOT FOUND THEN
                        IF TG_OP = 'DELETE' THEN
                            RETURN OLD;
                        END IF;
                        RAISE EXCEPTION 'Parent request does not exist'
                            USING ERRCODE = '23503';
                    END IF;

                    IF v_status <> 'draft' THEN
                        RAISE EXCEPTION 'Closed request membership cannot be modified'
                            USING ERRCODE = '23514',
                                  CONSTRAINT = 'ww_member_closed_guard';
                    END IF;

                    IF TG_OP = 'DELETE' THEN
                        RETURN OLD;
                    END IF;
                    RETURN NEW;
                END;
                $$;

                CREATE TRIGGER ww_member_write_biud
                BEFORE INSERT OR UPDATE OR DELETE
                ON workwear_requestemployee
                FOR EACH ROW
                EXECUTE FUNCTION ww_guard_member_write();


                CREATE FUNCTION ww_guard_assignment_write()
                RETURNS trigger LANGUAGE plpgsql AS $$
                DECLARE
                    v_member_id bigint;
                    v_request_id bigint;
                    v_status varchar(12);
                BEGIN
                    IF TG_OP = 'INSERT' THEN
                        v_member_id := NEW.request_employee_id;
                    ELSIF TG_OP = 'UPDATE' THEN
                        IF NEW.id IS DISTINCT FROM OLD.id
                           OR NEW.request_employee_id IS DISTINCT FROM OLD.request_employee_id
                           OR NEW.item_id IS DISTINCT FROM OLD.item_id
                        THEN
                            RAISE EXCEPTION 'Assignment identity is immutable'
                                USING ERRCODE = '23514',
                                      CONSTRAINT = 'ww_assignment_identity_immutable';
                        END IF;
                        v_member_id := OLD.request_employee_id;
                    ELSE
                        v_member_id := OLD.request_employee_id;
                    END IF;

                    SELECT request_id INTO v_request_id
                    FROM workwear_requestemployee
                    WHERE id = v_member_id;

                    IF NOT FOUND THEN
                        IF TG_OP = 'DELETE' THEN
                            RETURN OLD;
                        END IF;
                        RAISE EXCEPTION 'Parent membership does not exist'
                            USING ERRCODE = '23503';
                    END IF;

                    SELECT status INTO v_status
                    FROM workwear_request
                    WHERE id = v_request_id
                    FOR UPDATE;

                    IF NOT FOUND THEN
                        IF TG_OP = 'DELETE' THEN
                            RETURN OLD;
                        END IF;
                        RAISE EXCEPTION 'Parent request does not exist'
                            USING ERRCODE = '23503';
                    END IF;

                    IF v_status <> 'draft' THEN
                        RAISE EXCEPTION 'Closed request assignment cannot be modified'
                            USING ERRCODE = '23514',
                                  CONSTRAINT = 'ww_assignment_closed_guard';
                    END IF;

                    IF TG_OP = 'DELETE' THEN
                        RETURN OLD;
                    END IF;
                    RETURN NEW;
                END;
                $$;

                CREATE TRIGGER ww_assignment_write_biud
                BEFORE INSERT OR UPDATE OR DELETE
                ON workwear_requestassignment
                FOR EACH ROW
                EXECUTE FUNCTION ww_guard_assignment_write();


                CREATE FUNCTION ww_guard_request_lifecycle()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        IF OLD.status = 'closed' THEN
                            RAISE EXCEPTION 'Closed request cannot be deleted'
                                USING ERRCODE = '23514',
                                      CONSTRAINT = 'ww_request_closed_delete_guard';
                        END IF;
                        RETURN OLD;
                    END IF;

                    IF OLD.status = 'closed'
                       AND NEW.status = 'closed'
                       AND NEW IS DISTINCT FROM OLD
                    THEN
                        RAISE EXCEPTION 'Closed request cannot be edited'
                            USING ERRCODE = '23514',
                                  CONSTRAINT = 'ww_request_closed_update_guard';
                    END IF;

                    IF NEW.status IS DISTINCT FROM OLD.status
                       AND NEW.version <> OLD.version + 1
                    THEN
                        RAISE EXCEPTION 'Status transition must increment version'
                            USING ERRCODE = '23514',
                                  CONSTRAINT = 'ww_request_transition_version_guard';
                    END IF;

                    IF OLD.status = 'draft' AND NEW.status = 'closed' THEN

                        IF NOT EXISTS (
                            SELECT 1
                            FROM workwear_requestemployee m
                            JOIN workwear_requestassignment a
                              ON a.request_employee_id = m.id
                            WHERE m.request_id = NEW.id
                        ) THEN
                            RAISE EXCEPTION 'Request has no assignments'
                                USING ERRCODE = '23514',
                                      CONSTRAINT = 'ww_request_nonempty_guard';
                        END IF;

                        IF EXISTS (
                            SELECT 1
                            FROM workwear_requestemployee m
                            WHERE m.request_id = NEW.id
                              AND (
                                  m.snapshot_full_name = ''
                                  OR m.snapshot_personnel_number = ''
                                  OR m.snapshot_organization_name
                                     IS DISTINCT FROM NEW.organization_name
                                  OR NOT EXISTS (
                                      SELECT 1
                                      FROM workwear_requestassignment a
                                      WHERE a.request_employee_id = m.id
                                  )
                              )
                        ) THEN
                            RAISE EXCEPTION 'Request members are incomplete'
                                USING ERRCODE = '23514',
                                      CONSTRAINT = 'ww_request_member_completeness_guard';
                        END IF;

                    END IF;

                    RETURN NEW;
                END;
                $$;

                CREATE TRIGGER ww_request_lifecycle_bud
                BEFORE UPDATE OR DELETE
                ON workwear_request
                FOR EACH ROW
                EXECUTE FUNCTION ww_guard_request_lifecycle();


                CREATE FUNCTION ww_reject_structure_truncate()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'Workwear structural TRUNCATE is forbidden'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ww_structure_no_truncate';
                    RETURN NULL;
                END;
                $$;

                CREATE TRIGGER ww_request_no_truncate
                BEFORE TRUNCATE ON workwear_request
                FOR EACH STATEMENT
                EXECUTE FUNCTION ww_reject_structure_truncate();

                CREATE TRIGGER ww_member_no_truncate
                BEFORE TRUNCATE ON workwear_requestemployee
                FOR EACH STATEMENT
                EXECUTE FUNCTION ww_reject_structure_truncate();

                CREATE TRIGGER ww_assignment_no_truncate
                BEFORE TRUNCATE ON workwear_requestassignment
                FOR EACH STATEMENT
                EXECUTE FUNCTION ww_reject_structure_truncate();
            """,
            reverse_sql=r"""
                DROP TRIGGER ww_assignment_no_truncate
                ON workwear_requestassignment;

                DROP TRIGGER ww_member_no_truncate
                ON workwear_requestemployee;

                DROP TRIGGER ww_request_no_truncate
                ON workwear_request;

                DROP FUNCTION ww_reject_structure_truncate();

                DROP TRIGGER ww_request_lifecycle_bud
                ON workwear_request;

                DROP FUNCTION ww_guard_request_lifecycle();

                DROP TRIGGER ww_assignment_write_biud
                ON workwear_requestassignment;

                DROP FUNCTION ww_guard_assignment_write();

                DROP TRIGGER ww_member_write_biud
                ON workwear_requestemployee;

                DROP FUNCTION ww_guard_member_write();
            """,
        ),
        migrations.RunSQL(
            sql=r"""
CREATE FUNCTION ww_guard_request_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'New requests must start as drafts'
            USING ERRCODE = '23514', CONSTRAINT = 'ww_request_initial_draft_guard';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER ww_request_initial_bi
BEFORE INSERT ON workwear_request
FOR EACH ROW EXECUTE FUNCTION ww_guard_request_insert();
""",
            reverse_sql=r"""
DROP TRIGGER ww_request_initial_bi ON workwear_request;
DROP FUNCTION ww_guard_request_insert();
""",
        ),
    ]
