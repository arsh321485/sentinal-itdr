FROM python:3.11-slim

RUN groupadd -r forge && useradd -r -g forge -u 1000 forge

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R forge:forge /app
USER forge

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
