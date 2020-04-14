FROM python

ADD requirements.txt /app/requirements.txt

WORKDIR /app/

RUN pip install -r requirements.txt

ADD logging.config /app/logging.config
ADD static/ /app/static/
ADD app.py /app/app.py

#CMD ["uvicorn", "--host", "0.0.0.0", "--workers", "8", "--log-config", "logging.config", "app:app"]
CMD ["uvicorn", "--host", "0.0.0.0", "--workers", "1", "--log-level", "debug", "app:app"]
