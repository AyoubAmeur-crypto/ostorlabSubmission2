FROM python:3.12.7-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# Dependencies first so source edits do not invalidate the pip layer.
COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=1
RUN if [ "$INSTALL_DEV" = "1" ]; then \
        pip install -r requirements-dev.txt; \
    else \
        pip install -r requirements.txt; \
    fi

# In compose the source is bind-mounted over /app; the copy keeps the image
# usable on its own (CI, one-off runs).
COPY . .

EXPOSE 8080
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
