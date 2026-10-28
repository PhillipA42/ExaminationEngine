import re
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.contrib.sessions.backends.base import UpdateError
from django.contrib.sessions.exceptions import SessionInterrupted
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers
from django.utils.module_loading import import_string

from authentication.models import BrowserTabSession

TAB_SESSION_PARAM = "__tab_session__"
TAB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
TAB_SESSION_BOOTSTRAP = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Opening secure session</title></head>
<body><p>Opening your secure session...</p><script>
(() => {
    const url = new URL(window.location.href);
    const tabId = __TAB_ID__;
    url.searchParams.set('__tab_session__', tabId);
    window.location.replace(url.toString());
})();
</script></body></html>"""


def _extract_tab_id(request):
    for tab_id in (
        request.POST.get(TAB_SESSION_PARAM, ""),
        request.GET.get(TAB_SESSION_PARAM, ""),
        request.headers.get("X-Tab-Session", ""),
    ):
        if TAB_ID_PATTERN.fullmatch(tab_id):
            return tab_id
    return None


class TabSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.SessionStore = import_string(f"{settings.SESSION_ENGINE}.SessionStore")

    def __call__(self, request):
        tab_id = _extract_tab_id(request)
        if (
            not tab_id
            and request.method == "GET"
            and "text/html" in request.headers.get("Accept", "")
            and not request.path.startswith(("/static/", "/media/"))
        ):
            tab_id = secrets.token_urlsafe(24)
            return HttpResponse(
                TAB_SESSION_BOOTSTRAP.replace("__TAB_ID__", f'"{tab_id}"')
            )
        self.process_request(request)
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_request(self, request):
        tab_id = _extract_tab_id(request)
        if not tab_id:
            return

        request.META["SHARED_SESSION"] = request.session
        binding, created = BrowserTabSession.objects.get_or_create(
            tab_id=tab_id,
            defaults={"session_key": ""},
        )
        if created or not binding.session_key:
            session = self.SessionStore()
            session.save()
            binding.session_key = session.session_key
            binding.save(update_fields=["session_key", "updated_at"])
        else:
            session = self.SessionStore(session_key=binding.session_key)
            if not session.exists(binding.session_key):
                session = self.SessionStore()
                session.save()
                binding.session_key = session.session_key
                binding.save(update_fields=["session_key", "updated_at"])

        request.session = session
        request.META["TAB_SESSION_BINDING"] = binding
        request.META["TAB_SESSION_ID"] = tab_id

    def process_response(self, request, response):
        binding = request.META.get("TAB_SESSION_BINDING")
        if binding is None:
            return response

        if request.session.is_empty():
            request.session = self.SessionStore()
            request.session.save()
            binding.session_key = request.session.session_key
        else:
            if request.session.accessed:
                patch_vary_headers(response, ("Cookie",))
            if (
                request.session.modified or settings.SESSION_SAVE_EVERY_REQUEST
            ) and response.status_code < 500:
                try:
                    request.session.save()
                except UpdateError as exc:
                    raise SessionInterrupted(
                        "The tab session was deleted before the request completed."
                    ) from exc
            binding.session_key = request.session.session_key

        binding.save(update_fields=["session_key", "updated_at"])
        response.cookies.pop(settings.SESSION_COOKIE_NAME, None)

        if response.status_code >= 300 and response.status_code < 400:
            location = response.get("Location")
            if location:
                parts = urlsplit(location)
                query = [
                    (key, value)
                    for key, value in parse_qsl(parts.query, keep_blank_values=True)
                    if key != TAB_SESSION_PARAM
                ]
                query.append((TAB_SESSION_PARAM, request.META["TAB_SESSION_ID"]))
                response["Location"] = urlunsplit(
                    (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
                )
        request.session = request.META["SHARED_SESSION"]
        return response


class TabSessionCookieCleanupMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.META.get("TAB_SESSION_ID"):
            response.cookies.pop(settings.SESSION_COOKIE_NAME, None)
            response.cookies.pop(settings.CSRF_COOKIE_NAME, None)
        return response
