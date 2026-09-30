# app_templates.py

# --- Dockerfile Content ---
DOCKERFILE_CONTENT = """
# Use an official Python runtime as a parent image
FROM python:3.9-slim-buster

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY requirements.txt .
COPY . .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port {container_port} available to the world outside this container
EXPOSE {container_port}

# Define environment variable
ENV NAME {app_name}

# Run app.py when the container launches
CMD ["python", "app.py", "--port", "{container_port}"]
"""

# --- Kubernetes Manifest Templates ---
DEPLOYMENT_TEMPLATE = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {app_name}-deployment
  labels:
    app: {app_name}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {app_name}
  template:
    metadata:
      labels:
        app: {app_name}
    spec:
      containers:
      - name: {app_name}
        image: {image_full_name}:{image_tag}
        imagePullPolicy: IfNotPresent
        envFrom:
        - secretRef:
            name: mqtt-credentials
        ports:
        - containerPort: {container_port}
"""

SERVICE_TEMPLATE = """
apiVersion: v1
kind: Service
metadata:
  name: {app_name}-service
spec:
  selector:
    app: {app_name}
  ports:
    - protocol: TCP
      port: {service_port}
      targetPort: {container_port}
  type: LoadBalancer # Use NodePort or LoadBalancer for local K8s like Docker Desktop
"""
