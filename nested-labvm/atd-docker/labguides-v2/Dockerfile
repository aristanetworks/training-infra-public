FROM python:3.8.1

COPY ./src/* .

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y make git python3-pip python3-git

RUN pip3 install -r requirements.txt

CMD ["python3", "lgbuild.py"]
