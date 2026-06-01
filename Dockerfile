FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Install xvfb so we can run Playwright with headless=False in a headless container
USER root
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces requires running as a non-root user (UID 1000)
USER 1000
ENV HOME=/home/pwuser \
    PATH=/home/pwuser/.local/bin:$PATH \
    DISPLAY=:99

WORKDIR $HOME/app

# Install dependencies
COPY --chown=1000:1000 requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY --chown=1000:1000 . .

EXPOSE 7860

# Start Xvfb in background, then launch Uvicorn directly.
# This ensures Uvicorn responds to health checks immediately
# while Xvfb is available in the background for Playwright.
CMD bash -c "Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp & sleep 1 && python -m uvicorn server:app --host 0.0.0.0 --port 7860"
