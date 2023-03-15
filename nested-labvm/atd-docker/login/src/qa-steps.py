#!/usr/bin/env python
from base64 import b64decode, b64encode
import time
import jsonrpclib
import yaml
import ssl
import sys
import traceback 
import json 
import requests
TOPO_API = 'atd-conftopo'
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context


labACCESS = '/etc/atd/ACCESS_INFO.yaml'


def readLabDetails():
    # get the lab password and the topolgy in use
    with open(labACCESS) as f:
        labDetails = yaml.load(f,Loader=yaml.FullLoader)
    return labDetails['login_info']['jump_host']['pw'], labDetails['topology']



def readAtdTopo(labTopology):
    #get a list of all IP addresses in the topology
    with open("/opt/atd/topologies/"+ labTopology +"/topo_build.yml") as f:
        topology = yaml.load(f,Loader=yaml.FullLoader)
    #   print(topology)
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


def encodeID(tmp_data):
    tmp_str = json.dumps(tmp_data).encode()
    enc_str = b64encode(tmp_str).decode()
    return(enc_str)

def getAPI(action):
    try:
        _action = encodeID(action)
        response = requests.get(f"http://{TOPO_API}:50010/td-api/conftopo?action={_action}")
        return(json.loads(response.text))
    except Exception as e:
        print("Error calling backend API.")
        traceback.print_exc()
        print("Message: {err}".format( 
            err = str(e), 
        ))

def main():
    status = 0
    switches={}
    labPassword, labTopology = readLabDetails()
    allHostsIP, allHostsName = readAtdTopo(labTopology)
    try:
        for name, ip in zip(allHostsName,allHostsIP):
            name=name.replace('-','')
            switches[name]={}
            switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = ip))
            switches[name]["hostname"]=switch.runCmds(1,["show hostname"])[0]["hostname"]
            switches[name]["ztp_mode"]=switch.runCmds(1,["show zerotouch"])[0]['mode']
            if name != switches[name]['hostname'] or switches[name]['ztp_mode']!='disabled':
                return("failed")
        switches["cvp_status"]=getAPI("cvp_tasks")
        return ("success")
    except Exception as e:
        print(e)
        return("failed")
print(main())                                                                                                                                                                                                       