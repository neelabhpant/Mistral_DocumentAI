"""Entry point for the Cloudera AI Job "AP pipeline": process invoices that landed in S3 since the last run,
then rerun the rules. (Jobs run a script file in an IPython kernel; the working directory is the project root.)"""

import sys

import ensure_deps

ensure_deps.ensure()

from ap import pipeline  # noqa: E402

sys.argv = ["ap.pipeline", "--pending"]
pipeline.main()
