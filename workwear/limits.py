"""Ограничения вычислительной стоимости Workwear, общие для API и сервисов.

Это не разрешения пользователя и не замена FK/guards. Sync имеет отдельный
ограниченный бюджет: до1000×30 ID, менее1MiB даже с64-битными ID. Обычные
маршруты остаются64KiB; repeat-scan сохраняет предел1200 пар на запрос.
Клиент получает эти значения через metadata и делит read-only операции.
"""
MAX_REQUEST_MEMBERS = 1000
MAX_ITEMS_PER_MEMBER = 30
MAX_REPEAT_PAIRS = 1200
MAX_JSON_BYTES = 64 * 1024
MAX_SYNC_JSON_BYTES = 1024 * 1024
WRITE_BATCH_SIZE = 1000
