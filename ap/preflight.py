"""Check that this project can run the demo: Python version, Mistral API key and OCR model, the Impala data
connection, and write access to the data lake. Prints every problem at once with what to change.

Usage: python -m ap.preflight   (the AMP setup Job runs it first)
"""

import json
import os
import sys

from ap import lake
from ap.config import DB, IMPALA_CONNECTION, S3_ROOT

FORM_VARS = ("MISTRAL_API_KEY", "AP_IMPALA_CONNECTION", "AP_DB", "AP_S3_ROOT")  # the AMP launch form


def _form() -> str:
    """Some Cloudera AI versions save a value typed into the AMP's Configure Project form as the browser's input event
    (a JSON object) instead of the text. Caught first, before any check acts on such a value."""
    try:
        import cmlapi

        env = json.loads(cmlapi.default_client().get_project(os.environ["CDSW_PROJECT_ID"]).environment or "{}")
    except Exception as e:  # not on Cloudera AI, or the API is unreachable: nothing to check
        return f"Form values not checked ({str(e)[:100]})"
    bad = [k for k in FORM_VARS if k in env and not isinstance(env[k], str)]
    if bad:
        raise RuntimeError(f"The Configure Project form saved {', '.join(bad)} as a browser event instead of the text you "
                           "typed. Set them in Project Settings > Advanced, then retry the failed step.")
    return "Project environment variables are plain text"


def _python() -> str:
    if sys.version_info < (3, 11):
        raise RuntimeError(f"Python {sys.version.split()[0]}; requirements.txt needs 3.11+. Use the Python 3.12 runtime.")
    return f"Python {sys.version.split()[0]}"


def _mistral() -> str:
    from test_mistral_api import OCR_MODEL, get_client

    try:
        client = get_client()
    except SystemExit as e:  # get_client exits with an explanation when no key is set
        raise RuntimeError(str(e)) from None
    try:
        ids = {m.id for m in client.models.list().data or []}
    except Exception as e:
        raise RuntimeError(f"Mistral API call failed (check MISTRAL_API_KEY and egress to api.mistral.ai): {e}") from None
    if OCR_MODEL not in ids:
        raise RuntimeError(f"{OCR_MODEL} is not available to this Mistral API key")
    return f"Mistral API key works, {OCR_MODEL} available"


def _impala() -> str:
    try:
        lake.query("SELECT 1 AS ok")
    except Exception as e:
        try:
            import cml.data_v1 as cmldata

            names = sorted(c["name"] for c in cmldata.get_project_dataconnections() if c.get("type") == "IMPALA")
        except Exception:
            names = []
        hint = f" Impala connections in this project: {', '.join(names)}." if names else ""
        raise RuntimeError(f"Impala connection '{IMPALA_CONNECTION}' failed ({str(e)[:200]}). Set AP_IMPALA_CONNECTION "
                           f"to one of this project's Impala data connections.{hint}") from None
    return f"Impala connection '{IMPALA_CONNECTION}' works (database {DB})"


def _lake() -> str:
    if not S3_ROOT:
        raise RuntimeError("No data lake location: /etc/hadoop/conf/hive-site.xml has no warehouse directory. "
                           "Set AP_S3_ROOT, for example s3a://<bucket>/user/<you>/docai.")
    lake.hdfs("-mkdir", "-p", S3_ROOT)
    return f"Data lake folder {S3_ROOT} is writable"


def check() -> None:
    try:
        print(f"  OK    {_form()}")
    except RuntimeError as e:  # the other checks would act on the bad values
        print(f"  FAIL  Form values: {e}")
        raise
    failed = 0
    for name, fn in [("Python", _python), ("Mistral", _mistral), ("Impala", _impala), ("Data lake", _lake)]:
        try:
            print(f"  OK    {fn()}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
    if failed:  # an exception (not sys.exit) so the Job run is marked failed in the IPython kernel
        raise RuntimeError(f"{failed} check(s) failed; fix the project environment variables (Project Settings > Advanced) "
                           "and run the job again.")


if __name__ == "__main__":
    print("Preflight checks")
    try:
        check()
    except RuntimeError as e:
        sys.exit(str(e))
