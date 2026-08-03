"""Provenance-backed real-world case: hardcoded SECRET_KEY — CLEAN control.

Advisory:          CVE-2023-43791 / GHSA-f475-x83m-rx5m
                    https://github.com/advisories/ghsa-f475-x83m-rx5m

This is the clean counterpart of labelstudio_hardcoded_secret_vulnerable.py,
representing the fix pattern the advisory and Label Studio >=1.8.2 both
converged on: load SECRET_KEY from the environment instead of a literal.
This is NOT a copy of Label Studio's actual post-fix settings.py (which
wasn't available to verify verbatim); it is a representative clean control
of the same shape as the vulnerable file, used to check secondpass doesn't
raise a hardcoded_secret false positive on env-var-based config loading.
"""

import os

DEBUG = False

ALLOWED_HOSTS: list[str] = ["*"]

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(os.path.dirname(__file__), "db.sqlite3"),
    }
}
