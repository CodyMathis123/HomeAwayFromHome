FROM python:3.13-alpine
WORKDIR /app
COPY --chmod=0644 app.py ./
USER 65534:65534
EXPOSE 8443
CMD ["python", "-B", "app.py"]
