#!/bin/bash

echo "Starting atdStartup"
sudo curl -o /etc/atd/base_topo.yml https://raw.githubusercontent.com/aristanetworks/training-infra-public/nested-release/topologies/base_topo.yml
TOPO=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value topology)
APWD=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value login_info.jump_host.pw)
PROJECT=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value project)
CVP_VER=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value cvp)
CVP_VER_MOD=$(echo "$CVP_VER" | sed 's/\./\\./g')
MACHINE_NAME=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value name)

if [[ "$MACHINE_NAME" =~ -ex-[A-Za-z0-9]{4}(-[0-9]-[A-Za-z0-9]) ]]; then
    exam_code="${BASH_REMATCH[1]}"
    declare -A duration_map
    duration_map["-1-2"]=120  # 120 minutes (2 hours)
    duration_map["-2-2"]=240  # 240 minutes (4 hours)
    duration_map["-3-d"]=240  # 240 minutes (4 hours)
    duration_map["-5-3"]=240  # 240 minutes (4 hours)
    duration_map["-4-1"]=240  # 240 minutes (4 hours)
    duration_map["-1-v"]=120  # 120 minutes (2 hours)
    duration_map["-1-f"]=120  # 120 minutes (2 hours)
    duration_map["-5-d"]=240  # 120 minutes (4 hours)
    duration_map["-6-d"]=240  # 120 minutes (4 hours)
    # Get duration based on exam_code
    duration=${duration_map[$exam_code]}
    echo "Exam Duration: ${duration:-Unknown}"
    if grep -q "exam_duration:" /etc/atd/ACCESS_INFO.yaml; then
        # Update existing value using sed
        sed -i "s/exam_duration:.*$/exam_duration: ${duration:-Unknown}/" /etc/atd/ACCESS_INFO.yaml
    else
        # Add new entry if it doesn't exist
        echo "exam_duration: ${duration:-Unknown}" >> /etc/atd/ACCESS_INFO.yaml        
    fi
    if grep -q "examButtonNeeded:" /etc/atd/ACCESS_INFO.yaml; then
        sed -i "s/examButtonNeeded:.*$/examButtonNeeded: True/" /etc/atd/ACCESS_INFO.yaml
    else
        echo "examButtonNeeded: True" >> /etc/atd/ACCESS_INFO.yaml
    fi
    echo "current topology is exam topology :  $MACHINE_NAME , exam duration: $duration "
else
    if grep -q "exam_duration:" /etc/atd/ACCESS_INFO.yaml; then
        # Update existing value using sed
        sed -i "s/exam_duration:.*$/exam_duration: 0/" /etc/atd/ACCESS_INFO.yaml
    else
        # Add new entry if it doesn't exist
        echo "exam_duration: 0" >> /etc/atd/ACCESS_INFO.yaml        
    fi
    if grep -q "examButtonNeeded:" /etc/atd/ACCESS_INFO.yaml; then
        sed -i "s/examButtonNeeded:.*$/examButtonNeeded: False/" /etc/atd/ACCESS_INFO.yaml
    else
        echo "examButtonNeeded: False" >> /etc/atd/ACCESS_INFO.yaml
    fi
fi

EOS_TYPE=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value eos_type)
if [ "$EOS_TYPE" == "container-labs" ]; then
    EOS_TYPE="ceos"
fi
LABGUIDE_FILENAME_URL=$(cat /opt/atd/topologies/metadata.yml | python3 -m shyaml get-value topologies.$PROJECT.$TOPO.labguide_zipfile_url)
if [ "$PROJECT" == "atd-testdrivetraining-prod" ]; then
    PROJECT="prod"
else
    PROJECT="dev"
fi
NEW_BRANCH_NAME=$(cat /etc/atd/base_topo.yml | python3 -m shyaml get-value topologies.$TOPO.$PROJECT.$EOS_TYPE.cvp.$CVP_VER_MOD.branch)
if [ $? -eq 0 ]; then
  sed -i "/atd-public-branch/catd-public-branch: $NEW_BRANCH_NAME" /etc/atd/ATD_REPO.yaml
  echo "changing branch name to $NEW_BRANCH_NAME"
else
    echo "not changing any branch name"
fi

PLATFORM=$(cat /etc/atd/base_topo.yml | python3 -m shyaml get-value topologies.$TOPO.platform)
if [ $? -eq 0 ] && [ "$PLATFORM" == "cloudeos" ]; then
    echo "doing NAT in this topology"
    sudo sysctl -w net.ipv4.ip_forward=1
    sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
    sudo iptables -A FORWARD -i vmgmt -o eth0 -j ACCEPT
    sudo iptables -A FORWARD -m state --state ESTABLISHED,RELATED -o vmgmt -j ACCEPT
else
    echo "not doing NAT here"
fi

IFS="/" read -ra url_parts <<< "$LABGUIDE_FILENAME_URL"
LABGUIDE_FILENAME="${url_parts[-1]}"

LABGUIDE_DIRECTORY="/opt/labguides/web/"
if [ ! -d "$LABGUIDE_DIRECTORY" ]; then
  mkdir -p "$LABGUIDE_DIRECTORY"
  echo "Directory created: $LABGUIDE_DIRECTORY"
fi
if [ -e "${LABGUIDE_DIRECTORY}${LABGUIDE_FILENAME}" ]; then
  echo "File ${LABGUIDE_FILENAME} already exists. Nothing to do."
else
  # Remove all files in the target directory
  rm -rf "${LABGUIDE_DIRECTORY}"*
  # Download the file from the source URL to the target directory
  gsutil cp "${LABGUIDE_FILENAME_URL}" "${LABGUIDE_DIRECTORY}"
  cd $LABGUIDE_DIRECTORY
  tar -xzf "${LABGUIDE_FILENAME}"
  echo "File ${LABGUIDE_FILENAME} downloaded to ${LABGUIDE_DIRECTORY}"
fi

if [ "$(cat /etc/atd/ACCESS_INFO.yaml | grep eos_type)" ]
then
    EOS_TYPE=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value eos_type)
else
    EOS_TYPE=veos
fi

# Update ssh-key in EOS configlet for Arista user
ARISTA_SSH=$(cat /home/arista/.ssh/id_rsa.pub)

sed -i "/username arista ssh-key/cusername arista ssh-key ${ARISTA_SSH}" /opt/atd/topologies/$TOPO/configlets/ATD-INFRA

# Update arista user password for Guacamole

find /opt/atd/nested-labvm/atd-docker/*  -type f -print0 | xargs -0 sed -i "s/{ARISTA_REPLACE}/$APWD/g" 
find /opt/atd/topologies/$TOPO/files/*  -type f -print0 | xargs -0 sed -i "s/{ARISTA_REPLACE}/$APWD/g" 


# Perform check to see if docker auth file exists
if ! [ -f "/home/atdadmin/.docker/config.json" ]
then
    echo "Docker auth file not found, creating..."
    gcloud auth configure-docker gcr.io,us.gcr.io --quiet
    su atdadmin -c "gcloud auth configure-docker gcr.io,us.gcr.io --quiet"
fi

# Update the base configlets for ceos/veos mgmt numbering

if [ "$EOS_TYPE" = 'ceos' ] || [ "$EOS_TYPE" = 'container-labs' ]; then
    sed -i 's/Management1/Management0/g' /opt/atd/topologies/$TOPO/configlets/*
fi

# Copy topo image to app directory
rsync -av /opt/atd/topologies/$TOPO/atd-topo.png /opt/atd/topologies/$TOPO/files/apps/uilanding

# Add files to arista home
rsync -av --update /opt/atd/topologies/$TOPO/files/ /home/arista/arista-dir
rsync -av /opt/atd/topologies/$TOPO/files/infra /home/arista/

# Perform check if there is a scripts directory
if [ -d "/opt/atd/topologies/$TOPO/files/scripts" ]
then
    rsync -av /opt/atd/topologies/$TOPO/files/scripts /home/arista/GUI_Desktop/
fi

# Perform a check for the repo directory for datacenter
if ! [ -d "/home/arista/arista-dir/apps/coder/labfiles/lab6/repo" ] && [ $TOPO == "datacenter" ]
then
    mkdir -p /home/arista/arista-dir/apps/coder/labfiles/lab6/repo
    cd /home/arista/arista-dir/apps/coder/labfiles/lab6/repo
    git init --bare
fi

chown -R arista:arista /home/arista

# Update ATD containers

cd /opt/atd/nested-labvm/atd-docker

# su atdadmin -c 'bash docker_build.sh'

# Setting arista user ids for coder container
export ArID=$(id -u arista)
export ArGD=$(id -g arista)
export AtID=$(id -u atdadmin) 
export AtGD=$(id -g atdadmin)

#docker container prune -f

# Use sed to replace the text
PLATFORM=$(cat /etc/atd/base_topo.yml | python3 -m shyaml get-value topologies.$TOPO.platform)
if [ $? -eq 0 ] && [ "$PLATFORM" == "cloudeos" ]; then
    FILE_PATH="docker-compose.yml"
    sudo sed -i'' 's|us.gcr.io/atd-testdrivetraining-dev/atddocker_coder:1.0.2|us.gcr.io/atd-testdrivetraining-dev/atddocker_coder:1.0.2-path-finder|g' /opt/atd/nested-labvm/atd-docker/docker-compose.yml
    docker compose up -d --remove-orphans --force-recreate
else
    docker compose up -d --remove-orphans --force-recreate
fi
echo 'y' | docker image prune

systemctl restart sshd

if [ -f "/opt/clab/scripts/containerlabs_setup.py" ]
then
    bash /opt/clab/scripts/veth-connection.sh >> /opt/clab/scripts/log.txt
fi
os_name=$(grep -i '^NAME=' /etc/os-release | cut -d'=' -f2 | tr -d '"')
# Check if the OS name is AlmaLinux
if [[ "$os_name" == "AlmaLinux" ]]; then
    sudo ip route del 192.168.0.0/24 dev vmgmt
    sudo ip route add 192.168.0.0/25 dev vmgmt
fi

# if cEOS Startup present, run it
if [ -f "/opt/ceos/scripts/.ceos.txt" ]
then
    while : ; do
        [[ -f "/opt/ceos/scripts/Startup.sh" ]] && break
        echo "Pausing until file exists."
        sleep 1
    done
    bash /opt/ceos/scripts/Startup.sh
fi
echo "Executing script to check unhealthy containers"
rsync -av /opt/atd/nested-labvm/services/atdStartup/unhealthyContainers.sh /usr/local/bin/
bash /usr/local/bin/unhealthyContainers.sh
rsync -av /opt/atd/nested-labvm/services/atdStartup/examSubmitContainer.sh /usr/local/bin/
rsync -av /opt/atd/nested-labvm/services/atdStartup/exam-submission-check.service /etc/systemd/system
rsync -av /opt/atd/nested-labvm/services/atdStartup/exam-submission-check.timer /etc/systemd/system
sudo systemctl daemon-reload
sudo systemctl enable exam-submission-check.timer
sudo systemctl start exam-submission-check.timer
