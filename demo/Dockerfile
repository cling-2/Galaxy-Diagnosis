# galaxy-diag Case 1 故障模拟演示镜像（离线构建，复用 deploy/ 离线介质）
#
# 网络约束：docker.io / ollama.com 不可达，故：
#   - 基础镜像用本地已有的 python:3.10-slim（pyproject 要求 >=3.10，满足）
#   - ollama + 模型 + Python 依赖全部走 deploy/offline/ 离线介质（install_offline.sh）
#   - APT 用阿里云镜像（mirrors.aliyun.com 可达）装系统依赖
#
# 构建：
#   cd galaxy-diag
#   docker build -t galaxy-diag-demo -f demo/Dockerfile .
#
# 运行：
#   docker run -it --rm --memory=12g --memory-swap=12g --cpus=6 --shm-size=1g galaxy-diag-demo

FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive

ARG APT_MIRROR=mirrors.aliyun.com

# 1. 系统依赖（阿里云镜像，构建时联网；coreutils 提供 df/top，procps 提供 ps，curl 用于就绪探测）
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        curl procps coreutils ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/galaxy-diag

# 2. 复制项目源码 + 离线部署介质
COPY src /opt/galaxy-diag/src
COPY config.yaml /opt/galaxy-diag/config.yaml
COPY requirements.txt /opt/galaxy-diag/requirements.txt
COPY README.md /opt/galaxy-diag/README.md
COPY deploy /opt/galaxy-diag/deploy

# 3. 离线安装 ollama + 导入模型 + 装 Python 依赖（断网可执行）
#    install_offline.sh 生成 venv/bin/galaxy-diag launcher（PYTHONPATH=src）
RUN bash deploy/install_offline.sh

# 4. 复制 demo 垫片 / fixture / 脚本
COPY demo/shims    /opt/galaxy-diag-demo/shims
COPY demo/fixtures /opt/galaxy-diag-demo/fixtures
COPY demo/scripts  /opt/galaxy-diag-demo/scripts
RUN chmod +x /opt/galaxy-diag-demo/shims/* /opt/galaxy-diag-demo/scripts/*.sh

# 5. 部署 fixture 到容器内真实路径（让工具采集到真实文件）
RUN mkdir -p /etc/galaxy /var/log/galaxy \
    && cp /opt/galaxy-diag-demo/fixtures/etc/galaxy/api.conf /etc/galaxy/api.conf \
    && cp /opt/galaxy-diag-demo/fixtures/var/log/galaxy/control.log  /var/log/galaxy/control.log \
    && cp /opt/galaxy-diag-demo/fixtures/var/log/galaxy/network.log  /var/log/galaxy/network.log \
    && cp /opt/galaxy-diag-demo/fixtures/var/log/galaxy/storage.log  /var/log/galaxy/storage.log

# 6. 前置垫片到 PATH（venv 的 bin 也前置，确保 galaxy-diag 命令可用）
ENV PATH="/opt/galaxy-diag-demo/shims:/opt/galaxy-diag-demo/scripts:/opt/galaxy-diag/venv/bin:${PATH}"

# 7. 创建 galaxy-diag 数据目录
RUN mkdir -p /root/.galaxy-diag

# 8. entrypoint：激活 venv、启动 ollama、前置 PATH，进入 bash
ENTRYPOINT ["/opt/galaxy-diag-demo/scripts/entrypoint.sh"]
CMD ["bash"]
