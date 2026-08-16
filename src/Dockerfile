FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY DB.py ledger.py ./

CMD ["python", "ledger.py", "--help"]