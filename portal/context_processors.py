"""Меню учитывает права Django. Доступ к страницам проверяется отдельно."""
from django.urls import reverse


APPLICATIONS = (
    {
        "key": "workwear",
        "icon": "shirt",
        "status": "working",
        "label": "Спецодежда",
        "description": "Создание заявок на спецодежду, подбор сотрудников и контроль повторных назначений.",
        "url_name": "workwear_page:index",
        "permission": "workwear.view_request",
    },
)


def platform_navigation(request):
    available = [
        {**app, "url": reverse(app["url_name"])}
        for app in APPLICATIONS
        if request.user.is_authenticated and request.user.has_perm(app["permission"])
    ]
    namespace = request.resolver_match.namespace if request.resolver_match else ""
    current = next((app for app in available if namespace in {app["key"], app["key"] + "_page"}), None)
    return {"platform_apps": available, "platform_current_app": current}
