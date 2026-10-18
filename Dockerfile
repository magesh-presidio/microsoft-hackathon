FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py seed.py .
ENV AUTH_MODE=entra
COPY static static
COPY kb kb
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8000"]
