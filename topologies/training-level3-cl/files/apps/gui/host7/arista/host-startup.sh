sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 4EB27DB2A3B88B8B
sudo apt-get update
sudo apt install openssh-server -y
sudo service ssh start
sudo apt-get install ufw -y
sudo ufw enable
sudo ufw allow 22
sudo ufw disable
useradd -rm -d /home/arista -s /bin/bash -g root -G sudo -u 1000 arista
echo 'arista:arista123' | chpasswd
service ssh start
sudo apt install ifenslave -y
sudo apt install iputils-ping -y
sudo add-apt-repository ppa:wireshark-dev/stable -y
sudo apt-get install filezilla -y
sudo apt-get install vsftpd -y
sudo service vsftpd restart