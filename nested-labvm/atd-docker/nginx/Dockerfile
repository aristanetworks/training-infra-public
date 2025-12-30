FROM nginx:latest

# Copy nginx configuration
COPY src/atd.conf /etc/nginx/conf.d/

# Copy unified header JavaScript
COPY src/js/ /etc/nginx/js/

# Copy certificate generation script
COPY src/atd-certs.sh /docker-entrypoint.d/

RUN mkdir /etc/nginx/certs

RUN chmod +x /docker-entrypoint.d/atd-certs.sh

EXPOSE 80 443