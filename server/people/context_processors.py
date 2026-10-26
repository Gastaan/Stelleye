def profile(request):
    user = getattr(request, "user", None)
    return {"profile": getattr(user, "profile", None) if user and user.is_authenticated else None}
