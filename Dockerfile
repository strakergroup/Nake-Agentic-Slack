# This can be used by docker-compose and k8s to mount the 'app' folder locally

# Multi-stage build
# https://fastapi.tiangolo.com/deployment/docker/#container-images
# https://pipenv.pypa.io/en/latest/basics/#pipenv-and-docker-containers

# First build stage - Build venv with pipenv
FROM python:3.11 as builder

RUN pip install --user pipenv

WORKDIR /build

# Tell pipenv to create venv in the current directory
ENV PIPENV_VENV_IN_PROJECT=1
COPY Pipfile Pipfile.lock /build/
RUN /root/.local/bin/pipenv sync


# Final build stage - Run the app
FROM python:3.11

# install ffmpeg
RUN apt-get update && apt-get install -y ffmpeg

WORKDIR /code

# Copy venv from the previous build stage
COPY --from=builder /build/.venv/ /venv/
# Activate venv
ENV PATH=/venv/bin:$PATH

COPY app app
# Removed as we can inject this as secrets file in k8s
# COPY .env ibm-credentials.env ./

# Do not run with root
RUN useradd -m -u 1001 -g 33 straker
USER straker

CMD ["/venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]

