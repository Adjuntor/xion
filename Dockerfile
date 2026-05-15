# Use official Python image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy files
COPY requirements.txt .
COPY config ./config
COPY xion.py .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the script
CMD ["python", "xion.py"]
