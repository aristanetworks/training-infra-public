#!/bin/bash

echo "Starting atdStartup"
sudo curl -o /etc/atd/base_topo.yml https://raw.githubusercontent.com/aristanetworks/training-infra-public/nested-release/topologies/base_topo.yml
TOPO=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value topology)
APWD=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value login_info.jump_host.pw)
PROJECT=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value project)
CVP_VER=$(cat /etc/atd/ACCESS_INFO.yaml | python3 -m shyaml get-value cvp)
CVP_VER_MOD=$(echo "$CVP_VER" | sed 's/\./\\./g')
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

/usr/local/bin/docker-compose up -d --remove-orphans --force-recreate

echo 'y' | docker image prune

systemctl restart sshd

if [ -f "/opt/clab/scripts/containerlabs_setup.py" ]
then
    bash /opt/clab/scripts/veth-connection.sh >> /opt/clab/scripts/log.txt
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
