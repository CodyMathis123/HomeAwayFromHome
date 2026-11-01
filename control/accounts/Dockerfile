FROM python:3.12-alpine
WORKDIR /app
COPY broker.py /app/broker.py
EXPOSE 8092
USER 65534:65534
CMD ["python3","-B","/app/broker.py"]
