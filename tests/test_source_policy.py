"""Проверяем ограничения на публикуемые SVG-ресурсы."""
from pathlib import Path

from django.test import SimpleTestCase

from scripts.check_source import inspect


class SvgSourcePolicyTests(SimpleTestCase):
    """Разрешаем только простой SVG в каталогах статических ресурсов."""

    def test_simple_svg_is_allowed_in_static_resources(self):
        content = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 2h20"/></svg>'
        self.assertEqual(inspect(Path("portal/static/portal/icons/example.svg"), content), [])

    def test_svg_is_rejected_outside_static_resources(self):
        content = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"/>'
        problems = inspect(Path("docs/example.svg"), content)
        self.assertIn("SVG разрешён только в каталогах статических ресурсов", problems)

    def test_svg_rejects_script_elements(self):
        content = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        problems = inspect(Path("portal/static/portal/icons/example.svg"), content)
        self.assertIn("SVG содержит недопустимый элемент", problems)

    def test_svg_rejects_event_attributes(self):
        content = b'<svg xmlns="http://www.w3.org/2000/svg"><path onclick="alert(1)" d="M2 2h20"/></svg>'
        problems = inspect(Path("portal/static/portal/icons/example.svg"), content)
        self.assertIn("SVG содержит недопустимый атрибут", problems)

    def test_svg_rejects_invalid_xml(self):
        problems = inspect(Path("portal/static/portal/icons/example.svg"), b"<svg>")
        self.assertIn("Некорректная структура SVG", problems)
