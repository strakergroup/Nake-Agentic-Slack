# Multi-stage build
# https://fastapi.tiangolo.com/deployment/docker/#container-images
# https://pipenv.pypa.io/en/latest/basics/#pipenv-and-docker-containers

# First build stage - Build venv with pipenv
FROM python:3.10 as builder

RUN pip install --user pipenv

WORKDIR /build

# Tell pipenv to create venv in the current directory
ENV PIPENV_VENV_IN_PROJECT=1
# Requires the ray_sdk and ray_logger whl file until the package is published
COPY Pipfile Pipfile.lock RAY_Python_SDK-0.0.14-py3-none-any.whl RAY_Logger-0.0.1-py3-none-any.whl /build/
RUN /root/.local/bin/pipenv sync


# Final build stage - Run the app
FROM python:3.10

WORKDIR /code

# Copy venv from the previous build stage
COPY --from=builder /build/.venv/ /venv/
# Activate venv
ENV PATH=/venv/bin:$PATH

COPY app app
COPY .env ibm-credentials.env ./

# Do not run with root
RUN useradd -m -u 1001 -g 33 straker
USER straker

CMD ["/venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
