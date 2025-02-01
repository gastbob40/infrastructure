# create docker with postgres & aws

FROM postgres:14

RUN  apt-get update && apt-get install -y curl unzip && \
        curl "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip" && \
        unzip awscliv2.zip && ./aws/install && \
        rm -rf awscliv2.zip aws && \
        apt-get clean && rm -rf /var/lib/apt/lists/*

COPY backup.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh

ENTRYPOINT ["/usr/local/bin/backup.sh"]