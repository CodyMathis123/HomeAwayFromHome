FROM python:3.13-alpine
WORKDIR /app
COPY --chmod=0644 app.py index.html app.js accounts.js computers.js readiness.js styles.css ./
USER 65534:65534
EXPOSE 8081
CMD ["python", "-B", "app.py"]
