FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* && useradd --create-home studio
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY static ./static
RUN mkdir /app/data && chown studio:studio /app/data
USER studio
ENV MIXFLOW_HOST=0.0.0.0
EXPOSE 8000
CMD ["python","app.py"]
