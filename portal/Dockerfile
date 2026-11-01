FROM python:3.13-alpine
WORKDIR /app
COPY app.py index.html architecture.html app.js architecture.js navigation.js styles.css theme.css ./
USER 65534:65534
EXPOSE 8080
CMD ["python", "-B", "app.py"]
