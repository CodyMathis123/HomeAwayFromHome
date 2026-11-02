FROM python:3.12-alpine
WORKDIR /app
COPY --chmod=0644 broker.py bootstrap.py /app/
EXPOSE 8092
USER 65534:65534
CMD ["python3","-B","/app/broker.py"]
