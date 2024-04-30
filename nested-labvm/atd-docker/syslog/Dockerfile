FROM ubuntu:14.04

ENV DEBIAN_FRONTEND noninteractive

ADD ./src/rsyslog.list /etc/apt/sources.list.d/rsyslog.list
RUN apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 0F6DD8135234BF2B
RUN apt-get update && \
    apt-get install -y rsyslog

# Create app structure
RUN mkdir -p /var/log/remote/
ADD ./src/rsyslog.conf /etc/rsyslog.conf
VOLUME /var/log/remote/
# Set the syslog user and group explicitly
RUN chmod -R 777 /var/log/remote/

EXPOSE 1514
EXPOSE 1514/udp
CMD /usr/sbin/rsyslogd -n -f /etc/rsyslog.conf