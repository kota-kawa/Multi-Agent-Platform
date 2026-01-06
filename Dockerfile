FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5050

ENV UVICORN_RELOAD=1

CMD ["uvicorn", "app:app", "--host=0.0.0.0", "--port=5050", "--reload"]
