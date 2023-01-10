# Multi-stage build
# 
# This can be used by docker-compose and k8s to mount the 'app' folder locally

# First build stage - Build venv with pipenv
FROM python:3.10 as builder

RUN pip install --user pipenv

WORKDIR /build

# Tell pipenv to create venv in the current directory
ENV PIPENV_VENV_IN_PROJECT=1
COPY Pipfile Pipfile.lock /build/
RUN /root/.local/bin/pipenv sync

# Final build stage - Run the app
FROM python:3.10

WORKDIR /code

# Copy venv from the previous build stage
COPY --from=builder /build/.venv/ /venv/
# Activate venv
ENV PATH=/venv/bin:$PATH

# Do not run with root
RUN useradd -m -u 1001 -g 33 straker
USER straker

CMD ["/venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
