"""Provenance-backed real-world case: Django note view missing ownership check.

Source repo:      https://github.com/thsteixeira/django-security-lab
Commit:            ab9e66e4b749397f2e76784111cf7685084dcf46
File (upstream):   labs/post_06_idor/views_vulnerable.py
License:           MIT
Write-up:          "Broken Access Control and IDOR" —
                    https://thiagoteixeira.tech/blog/broken-access-control-and-idor-when-logging-in-is-not-the-same-as-being-allowed/
CWE:               CWE-639 (Authorization Bypass Through User-Controlled Key)

Why the bug is visible in this single file alone:
The view is behind @login_required (authenticated) but the object lookup
`get_object_or_404(Note, pk=pk)` is scoped to the whole table, not to
request.user. Any authenticated user can read any other user's note by
walking sequential primary keys. Nothing in models.py or urls.py changes
this — the missing `owner=request.user` filter on this one line is the
entire vulnerability.

Docstring, imports, decorator, and the vulnerable lookup line are verbatim
from the upstream commit above. The inline HTML strings in the return value
were re-wrapped for this excerpt (the raw fetch tool used to pull the file
stripped angle brackets); the *logic* — the unscoped get_object_or_404 call
and the fields it renders — is unchanged from upstream.
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.html import escape

from .models import Note


@login_required
def note_detail(request, pk):
    # DANGER: the lookup is never scoped to request.user. `pk` alone decides
    # what comes back, so any authenticated user reads any note.
    note = get_object_or_404(Note, pk=pk)
    return HttpResponse(
        "<html><body>"
        "<h1>Vulnerable note</h1>"
        f"<p>Viewing as {escape(request.user.username)}</p>"
        f"<h2>{escape(note.title)}</h2>"
        f"<p>{escape(note.body)}</p>"
        "</body></html>"
    )
