"""Provenance-backed real-world case: Label Studio hardcoded Django SECRET_KEY.

Source repo:      https://github.com/HumanSignal/label-studio
Vulnerable ver:    1.8.1
File (upstream):   label_studio/core/settings/base.py (SECRET_KEY assignment)
License:           Apache-2.0
Advisory:          CVE-2023-43791 / GHSA-f475-x83m-rx5m
                    https://github.com/advisories/ghsa-f475-x83m-rx5m
CWE:               CWE-321 (Use of Hard-coded Cryptographic Key)

Why the bug is visible in this single file alone:
Django's SECRET_KEY signs session cookies (salted_hmac). A hardcoded literal
value here lets anyone who reads the source forge a valid session for any
user (the advisory demonstrates this with a management command that signs
its own session dict using the leaked key) — no other file is needed to see
or exploit the flaw.

Honesty note on provenance: the SECRET_KEY string below (including the
"SECURITY WARNING" comment above it) is quoted verbatim from GHSA-f475-x83m-
rx5m's own reproduction steps. Label Studio's actual settings.py is a large,
multi-hundred-line file with unrelated config; rather than lift the whole
file, this fixture reconstructs the minimal single-line flaw the advisory
describes, in a small settings-module shape. It is NOT a byte-for-byte copy
of upstream settings.py — only the SECRET_KEY line is a verbatim quote.
"""

import os

DEBUG = False

ALLOWED_HOSTS: list[str] = ["*"]

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "$(fefwefwef13;LFK{P!)@#*!)kdsjfWF2l+i5e3t(8a1n"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(os.path.dirname(__file__), "db.sqlite3"),
    }
}
