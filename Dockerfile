FROM python:3.13

# tools & fonts for nicer rendering if you ever headless-screenshot
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip fonts-liberation ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python deps
RUN pip install --no-cache-dir \
    robotframework \
    robotframework-seleniumlibrary \
    requests

# (Optional) for parsing/validating JSON in data-gen
# RUN pip install --no-cache-dir jsonschema

# keep container lean
WORKDIR /work
