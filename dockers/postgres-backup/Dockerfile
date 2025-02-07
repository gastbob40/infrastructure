# create docker with postgres & aws

FROM ubuntu:24.04

RUN  apt-get update && apt-get install -y curl unzip postgresql jq && \
    ARCH=$(uname -m) && \
        if [ "$ARCH" = "aarch64" ]; then \
            echo "Detected ARM64 architecture, installing AWS CLI for ARM64..." && \
            curl "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip"; \
        else \
            echo "Detected AMD64 architecture, installing AWS CLI for AMD64..." && \
            curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"; \
        fi && \
  unzip awscliv2.zip && ./aws/install && \
  apt-get clean && rm -rf /var/lib/apt/lists/*

COPY backup.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh

COPY glacier.sh /usr/local/bin/glacier.sh
RUN chmod +x /usr/local/bin/glacier.sh

ENTRYPOINT ["/usr/local/bin/backup.sh"]