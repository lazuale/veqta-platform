from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    return render(request, "portal/home.html", {"portal_is_home": request.path == "/"})


@login_required
def profile(request):
    return render(request, "portal/profile.html", {
        "profile_groups": request.user.groups.order_by("name"),
    })
