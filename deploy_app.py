"""Deploy the demo on Cloudera AI via the cmlapi v2 SDK: the Streamlit Application and the
"AP pipeline" Job (batch processing of invoices landed in S3).

Run from a Cloudera AI session in this project: python deploy_app.py
Re-running restarts the Application (picking up code changes) and updates the Job.
MISTRAL_API_KEY should be a project environment variable (Project Settings > Advanced); the Application and
Job inherit it, so the key is never copied into their settings or into files.
"""

import json
import os

import cmlapi

NAME = "Document AI: accounts payable (Cloudera + Mistral OCR 4)"
SUBDOMAIN = "docai-ap"
OLD_SUBDOMAINS = {"contract-ai"}  # earlier name of the same Application
JOB_NAME = "AP pipeline: process new invoices"
CPU, MEMORY_GB = 1, 4
KERNEL = "Python 3.12"  # requirements.txt pins need Python >= 3.11


def latest_runtime(client: cmlapi.CMLServiceApi) -> str:
    """Newest Standard PBJ Workbench runtime image for KERNEL."""
    flt = f'{{"kernel":"{KERNEL}","edition":"Standard","editor":"PBJ Workbench"}}'
    runtimes = client.list_runtimes(search_filter=flt, page_size=500).runtimes
    return max(runtimes, key=lambda r: r.image_identifier.rsplit(":", 1)[-1]).image_identifier


def deploy_app(client, project_id: str, runtime: str) -> None:
    apps = client.list_applications(project_id, page_size=100).applications
    app = next((a for a in apps if a.subdomain == SUBDOMAIN), None) or next(
        (a for a in apps if a.subdomain in OLD_SUBDOMAINS), None)
    if app:
        try:  # restart only works on a live run; a failed/stopped app must be stopped first
            client.stop_application(project_id, app.id)
        except cmlapi.rest.ApiException:
            pass
        client.update_application(
            cmlapi.Application(name=NAME, subdomain=SUBDOMAIN, runtime_identifier=runtime,
                               description="Invoices read by Mistral OCR 4, checked against Iceberg data, turned into actions",
                               environment="{}"),
            project_id, app.id)
        client.restart_application(project_id, app.id)
        print(f"Updated and restarted application {app.id}")
    else:
        app = client.create_application(
            cmlapi.CreateApplicationRequest(
                name=NAME, subdomain=SUBDOMAIN, script="launch_app.py", runtime_identifier=runtime,
                description="Invoices read by Mistral OCR 4, checked against Iceberg data, turned into actions",
                cpu=CPU, memory=MEMORY_GB),
            project_id)
        print(f"Created application {app.id}")
    print(f"App URL: https://{SUBDOMAIN}.{os.environ['CDSW_DOMAIN']}")


def deploy_job(client, project_id: str, runtime: str) -> None:
    jobs = client.list_jobs(project_id, page_size=100).jobs
    job = next((j for j in jobs if j.name == JOB_NAME), None)
    if job:
        client.update_job(cmlapi.Job(script="run_pipeline.py", runtime_identifier=runtime), project_id, job.id)
        print(f"Updated job {job.id}")
    else:
        job = client.create_job(
            cmlapi.CreateJobRequest(name=JOB_NAME, script="run_pipeline.py", runtime_identifier=runtime,
                                    cpu=CPU, memory=MEMORY_GB, timeout=3600),
            project_id)
        print(f"Created job {job.id} (manual trigger; add a schedule in the UI or let NiFi trigger it)")


def main() -> None:
    client = cmlapi.default_client()
    project_id = os.environ["CDSW_PROJECT_ID"]
    env = json.loads(client.get_project(project_id).environment or "{}")
    if not (env.get("MISTRAL_API_KEY") or env.get("MISTRAL_API")):
        print("Warning: MISTRAL_API_KEY is not a project environment variable; OCR calls will fail until it is.")
    runtime = latest_runtime(client)
    deploy_app(client, project_id, runtime)
    deploy_job(client, project_id, runtime)


if __name__ == "__main__":
    main()
