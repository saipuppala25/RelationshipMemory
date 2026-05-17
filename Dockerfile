FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

EXPOSE 5000

CMD ["python", "app.py"]
