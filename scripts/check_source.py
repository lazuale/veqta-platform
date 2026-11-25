#!/usr/bin/env python3
"""Проверяем исходники на секреты, приватные ключи и адреса внутренних сетей."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = {".git", ".venv", "venv", "__pycache__", "node_modules", "staticfiles", "media", "private", "data", "backups", "test-results", ".pytest_cache"}
ALLOWED_SUFFIXES = {".sh", ".py", ".js", ".css", ".html", ".md", ".sql", ".txt", ".lock", ".service", ".conf", ".example", ".yml", ".svg"}
SVG_STATIC_ROOTS = (Path("portal/static"), Path("workwear/static"))
SVG_ELEMENTS = {"svg", "g", "path", "circle", "rect", "line", "polyline", "polygon", "ellipse"}
SVG_ATTRIBUTES = {"width", "height", "viewBox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "d", "cx", "cy", "r", "x", "y", "x1", "y1", "x2", "y2", "points", "aria-hidden", "aria-label", "role"}
ALLOWED_NAMES = {".gitignore", ".gitattributes", ".python-version"}


def inspect_svg(path, text):
    """Разрешаем только простой SVG без скриптов и внешних ссылок."""
    problems = []
    if not any(path.is_relative_to(root) for root in SVG_STATIC_ROOTS):
        problems.append("SVG разрешён только в каталогах статических ресурсов")
    if re.search(r"<!\\s*(?:DOCTYPE|ENTITY)", text, re.IGNORECASE):
        return problems + ["SVG не должен содержать DTD или объявления сущностей"]
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return problems + ["Некорректная структура SVG"]
    namespace = "{http://www.w3.org/2000/svg}"
    if root.tag != namespace + "svg":
        problems.append("Корневой элемент должен быть SVG")
    for element in root.iter():
        if not isinstance(element.tag, str) or not element.tag.startswith(namespace):
            problems.append("SVG содержит элемент вне пространства имён SVG")
            continue
        name = element.tag[len(namespace):]
        if name not in SVG_ELEMENTS:
            problems.append("SVG содержит недопустимый элемент")
        for attribute, value in element.attrib.items():
            attr_name = attribute.rsplit("}", 1)[-1]
            if attr_name not in SVG_ATTRIBUTES:
                problems.append("SVG содержит недопустимый атрибут")
            if re.search(r"(?:javascript:|https?:|data:|url\\s*\\()", value, re.IGNORECASE):
                problems.append("SVG не должен содержать ссылки или исполняемые значения")
        if element.text and element.text.strip():
            problems.append("SVG не должен содержать текстовые узлы")
    return list(dict.fromkeys(problems))


def inspect(path, data):
    """Не читаем исключённые файлы при проверке публикации."""
    problems = []
    if path.suffix not in ALLOWED_SUFFIXES and path.name not in ALLOWED_NAMES:
        problems.append("Недопустимый тип публикуемого файла")
    if any(part in EXCLUDED for part in path.parts) or path.name.startswith(".env"):
        problems.append("В публикацию включён локальный файл")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return problems+["Ожидался текст UTF-8"]
    if path.suffix == ".svg":
        problems.extend(inspect_svg(path, text))
    if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", text):
        problems.append("Обнаружен приватный ключ")
    if re.search(r"\b(?:ghp|gho|ghs)_[A-Za-z0-9]{30,}\b", text):
        problems.append("Обнаружен токен доступа")
    for address in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        parts = [int(part) for part in address.split(".")]
        if parts[0] == 10 or parts[:2] == [192, 168] or parts[0] == 172 and 16 <= parts[1] <= 31:
            problems.append("Обнаружен адрес закрытой сети")
    return problems


def main():
    """Проверяем индекс Git, а до первого коммита — рабочий каталог."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    if args.staged:
        result = subprocess.check_output(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], cwd=ROOT)
        paths = [Path(name) for name in result.decode().split("\0") if name]
        contents = [(p, subprocess.check_output(["git", "show", f":{p.as_posix()}"], cwd=ROOT)) for p in paths]
    else:
        contents = []
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDED for part in relative.parts) or path.name.startswith(".env"):
                continue
            if path.is_symlink():
                raise SystemExit(f"Символическая ссылка не допускается: {relative}")
            if path.is_file():
                contents.append((relative, path.read_bytes()))
    failed = 0
    for path, data in contents:
        problems = inspect(path, data)
        if problems:
            failed += 1
            print(f"{path}: {'; '.join(problems)}")
    print(f"Проверено файлов: {len(contents)}; ошибок состава: {failed}")
    return bool(failed)


if __name__ == "__main__":
    sys.exit(main())
