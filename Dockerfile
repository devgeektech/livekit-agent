  GNU nano 7.2                                                       Dockerfile                                                                 
# Base image
FROM python:3.11-slim

# Keep everything as root
USER root

# Set working directory
WORKDIR /var/www/html/just-placed-backend

# Install system dependencies
RUN apt-get update && \
    apt-get install -y gcc python3-dev sqlite3 && \
    rm -rf /var/lib/apt/lists/*

# Copy Python dependencies and install
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy entire project
COPY . .

# Expose port if your agent serves HTTP/WebSocket
EXPOSE 8081

RUN python agent.py download-files
# Default command to start the agent
CMD ["python", "agent.py", "start"]

