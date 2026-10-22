"""Entry point for the Cloudera AI Job "AP setup" (the first AMP step): install requirements, check the project
can reach Mistral, Impala and the data lake, then create the Iceberg tables, load the synthetic enterprise data
from data/ap and land the batch invoice PDFs in S3. (Jobs run a script file in an IPython kernel; the working
directory is the project root.)"""

import sys

import ensure_deps

ensure_deps.ensure()

from ap import preflight, setup_lake  # noqa: E402

print("Preflight checks")
preflight.check()

sys.argv = ["ap.setup_lake", "--reset"]
setup_lake.main()
