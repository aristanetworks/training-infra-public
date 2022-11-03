#!/usr/bin/env python

# This script saves the running config of each switch to a local folder

import jsonrpclib
import yaml
import ssl
import os

import json
import requests


try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

requests.packages.urllib3.disable_warnings()

labACCESS = '/etc/atd/ACCESS_INFO.yaml'
regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
cvpHost = "192.168.0.5"
cvpUser = "arista"
url = "https://{host}".format(host=cvpHost)


def readLabDetails():
    # get the lab password and the topolgy in use
    with open(labACCESS) as f:
        labDetails = yaml.load(f,Loader=yaml.FullLoader)
    return labDetails['login_info']['jump_host']['pw'], labDetails['topology'],labDetails['name']



def readAtdTopo(labTopology):
    #get a list of all IP addresses in the topology
    with open("/opt/atd/topologies/"+ labTopology +"/topo_build.yml") as f:
        topology = yaml.load(f,Loader=yaml.FullLoader)
        mylist= topology['nodes']
        test=[]
        for item in mylist:
           test.append(list(item.keys()))
           hostsName = [item for sublist in test for item in sublist]
    hostsIP = []
    for a in topology['nodes']:
        for key in a.keys():
            hostsIP.append(a[key]['ip_addr'])
    return hostsIP, hostsName



def grabSwitchDetails(allHostsName,allHostsIP,folder,labPassword):
    pingDone = 0
    evpnNOTdone = 0
    for name, ip in zip(reversed(allHostsName),reversed(allHostsIP)):
        switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = ip))
        try:
            config = switch.runCmds(1,["enable", "show running-config"],"text")
            runConfig = (config[1]["output"])
        except Exception as e:
            print(str(e))
        else:
            filename = str(name) + "-running" + ".txt"
            completePath = os.path.join(folder, filename)
            with open(completePath, 'w') as f:
                f.write(runConfig)





def main():
    labPassword, labTopology, labName = readLabDetails()
    allHostsIP, allHostsName = readAtdTopo(labTopology)
    restarted = 0
    folder = "running-configs"
    if not os.path.exists(folder):
        try:
            os.makedirs(folder)
        except OSError as exc: # Guard against race condition
            raise
    grabSwitchDetails(allHostsName,allHostsIP,folder,labPassword)


main()


# TODO - Include persist folder to TAR file.