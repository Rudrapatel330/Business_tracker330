FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Install xvfb so we can run Playwright with headless=False in a headless container
USER root
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces requires running as a non-root user (UID 1000)
# The Playwright image already has a user with UID 1000 named "pwuser"
USER 1000
ENV HOME=/home/pwuser \
    PATH=/home/pwuser/.local/bin:$PATH

WORKDIR $HOME/app

# Install dependencies
COPY --chown=1000:1000 requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir uvicorn

# Copy the rest of the application
COPY --chown=1000:1000 . .

# Run the FastAPI server via Uvicorn, wrapped in xvfb-run to simulate a display
# Hugging Face Spaces exposes port 7860 by default
CMD ["xvfb-run", "-a", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
