FROM ghcr.io/coder/code-server:4.102.2-39

# set the environment correctly first, including path
USER coder
ENV HOME=/home/coder
ENV PATH=$PATH:/home/coder/.local/bin
ENV AVD_VER="5.7.0"
ENV CVP_VER="3.12.0"
ENV ANTA_VER="1.5.0"
# set args to cleanup installs
ARG PIP_DISABLE_PIP_VERSION_CHECK=1
ARG PIP_NO_CACHE_DIR=1
ARG PIP_BREAK_SYSTEM_PACKAGES=1


USER root
RUN dnf update -y && \
    dnf install -y python3 git openssh-server vim nano less rsync man-db jq python3-pip wget zsh sshpass libvirt-client grpcurl && \
    dnf clean all
USER coder


# Install Code extensions
COPY src/vs-extensions.txt /home/coder/

RUN while IFS= read -r line; do code-server --install-extension $line; done < /home/coder/vs-extensions.txt

RUN pip3 install --user pyeapi jsonrpclib-pelix shyaml && \
    pip3 install --user "ansible-core==2.18.9" --upgrade

# Install arista.avd, community.general and ansible.posix ansible-galaxy collections, Use this for version control of AVD topologies
RUN ansible-galaxy collection install arista.avd:==${AVD_VER} && \
    ansible-galaxy collection install arista.cvp:==${CVP_VER} --upgrade && \
    ansible-galaxy collection install community.general --upgrade && \
    ansible-galaxy collection install ansible.posix --upgrade

# Install Ansible-AVD collection requirements
RUN pip3 install --user "pyavd[ansible]==${AVD_VER}" "anta[cli]==${ANTA_VER}" --upgrade


COPY src/.gitconfig /home/coder/

RUN wget --quiet https://github.com/robbyrussell/oh-my-zsh/raw/master/tools/install.sh -O - | zsh || true \
    && echo 'plugins=(ansible common-aliases safe-paste git jsontools history git-extras)' >> $HOME/.zshrc \
    # redirect to &>/dev/null is required to silence `agent pid XXXX` message from ssh-agent
    && echo 'eval `ssh-agent -s` &>/dev/null' >> $HOME/.zshrc \
    && echo 'export TERM=xterm' >>  $HOME/.zshrc

RUN sudo usermod --shell /bin/zsh coder

WORKDIR /home/coder/project

RUN sudo chown -R coder:coder /home/coder/project

ENV SHELL /bin/zsh