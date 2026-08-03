"""Provenance-backed real-world case: Django note view — FIXED (owner-scoped).

Source repo:      https://github.com/thsteixeira/django-security-lab
Commit:            ab9e66e4b749397f2e76784111cf7685084dcf46
File (upstream):   labs/post_06_idor/views_secure.py
License:           MIT
Write-up:          https://thiagoteixeira.tech/blog/broken-access-control-and-idor-when-logging-in-is-not-the-same-as-being-allowed/
CWE:               CWE-639

This is the clean/fixed counterpart of django_idor_notes_vulnerable.py. The
only functional change is `owner=request.user` added to the lookup, so a
note belonging to another user simply isn't in the result set and Django
raises an indistinguishable 404. Used as a clean control: secondpass should
not accept a missing_ownership_check finding on this file.

Docstring, imports, decorator, and the scoped lookup line are verbatim from
the upstream commit above; inline HTML strings were re-wrapped as in the
vulnerable counterpart (see that file's note).
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.html import escape

from .models import Note


@login_required
def note_detail(request, pk):
    # The lookup is scoped to the requester. Another user's note does not
    # exist in this queryset, so the response is an indistinguishable 404.
    note = get_object_or_404(Note, pk=pk, owner=request.user)
    return HttpResponse(
        "<html><body>"
        "<h1>Secure note</h1>"
        f"<p>Viewing as {escape(request.user.username)}</p>"
        f"<h2>{escape(note.title)}</h2>"
        f"<p>{escape(note.body)}</p>"
        "</body></html>"
    )
