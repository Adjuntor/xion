# Use official Python image
FROM python:alpine

# Set working directory
WORKDIR /usr/src/app

# Copy files
COPY requirements.txt ./

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the script
CMD ["python", "-u", "./main.py"]
