# Galaxy-Diag 离线部署测试镜像
#
# 模拟断网客户机环境，验证 install_offline.sh + main.py 端到端流程。
# 基于 python:3.10-slim（已含 Python），仅补装系统依赖。
#
# 用法：
#   docker build -t galaxy-diag-test -f deploy/Dockerfile .
#   docker run --rm -it -v "${PWD}:/opt/galaxy-diag" --memory=12g --memory-swap=12g --cpus=6  --shm-size=1g galaxy-diag-test
#   # 容器内：bash deploy/install_offline.sh && source venv/bin/activate && galaxy-diag

FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive

ARG APT_MIRROR=mirrors.aliyun.com

# 系统依赖（构建时联网安装，真实部署需提前准备 deb 包或预装）
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        zstd \
        curl \
        procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/galaxy-diag

CMD ["/bin/bash"]
