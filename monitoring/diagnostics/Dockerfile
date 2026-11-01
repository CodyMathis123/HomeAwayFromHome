FROM python:3.13-alpine
WORKDIR /app
RUN pip install --no-cache-dir --only-binary=:all: cryptography==50.0.2
COPY app.py certificates.py ./
USER 65534:65534
EXPOSE 8095
CMD ["python", "-B", "app.py"]
