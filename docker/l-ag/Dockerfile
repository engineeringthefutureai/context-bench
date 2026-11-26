# L-AG image: the Antigravity SDK, docker/l-ag/solve.py (the agent) and model_proxy.py (the model-proxy service),
# nothing else of the project.
# Built and run by docker/l-ag/compose.yaml (the runner does it: context_bench/levels/l_ag_antigravity.py).
# Python 3.14, as on the host that scores the runs: repo M relies on 3.14's lazy annotations (a field named
# like the module its annotation uses), so 3.13 fails the repo's own tests where the scorer passes them.
FROM python:3.14-slim

# What a Python agent expects to find in a repo checkout: the test runner the fixtures' own tests use.
RUN pip install --no-cache-dir "google-antigravity==0.1.24" "pytest==8.4.2"

COPY solve.py model_proxy.py /opt/l-ag/

# The container runs as the host user (compose: user), so files it creates in /workspace stay removable.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp/home
WORKDIR /workspace
ENTRYPOINT ["python", "/opt/l-ag/solve.py"]
