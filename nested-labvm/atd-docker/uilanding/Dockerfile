FROM python:3.9


RUN pip install --upgrade pip

RUN pip install tornado requests ruamel.yaml

RUN mkdir -p /opt/topo/html

RUN mkdir -p /root/labguides

WORKDIR /root

RUN curl -sSL https://get.docker.com/ | sh

COPY ./src/html /opt/topo/html/

COPY ./src/uilanding.py /usr/local/bin/uilanding

RUN chmod +x /usr/local/bin/uilanding

EXPOSE 80

CMD ["uilanding"]

