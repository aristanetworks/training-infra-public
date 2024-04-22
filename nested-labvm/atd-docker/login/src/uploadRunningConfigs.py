#!/usr/bin/env python

# This script saves the running config of each switch to a local folder

import jsonrpclib
import yaml
import ssl
import os

import json
import requests

from google.cloud import storage

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


def readYaml(file=labACCESS):
    # get the lab password and the topolgy in use
    with open(file) as f:
        yaml_data = yaml.load(f,Loader=yaml.FullLoader)
    return yaml_data



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



def uploadSwitchDetails(allHostsName,allHostsIP,labDetails):
    pingDone = 0
    evpnNOTdone = 0
    #Bucket and details
    storage_client = storage.Client()
    bucket_name = labDetails["project"]+"-grading"
    bucket_folder_path = labDetails["name"]
    bucket_obj = storage_client.bucket(bucket_name)
    #Upload grading config
    if not bucket_obj.blob(f"{bucket_folder_path}/grading_config.yaml").exists():
        grading_config = yaml.dump({"to_be_graded": allHostsName, "templates": labDetails["labguides_modules"]}, default_flow_style=False)
        blob_obj = bucket_obj.blob(f"{bucket_folder_path}/grading_config.yaml")
        blob_obj.upload_from_string(grading_config, content_type="yaml")

    #get running configs
    for name, ip in zip(reversed(allHostsName),reversed(allHostsIP)):
        switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labDetails['login_info']['jump_host']['pw'], ipaddress = ip))
        try:
            config = switch.runCmds(1,["enable", "show running-config"],"text")
            runConfig = (config[1]["output"])
        except Exception as e:
            print(str(e))
        else:
            filename = str(name) + "-running" + ".txt"
            blob_obj = bucket_obj.blob(f"{bucket_folder_path}/running-configs/{filename}")
            #blob_obj.upload_from_filename(bucket_destination)
            blob_obj.upload_from_string(runConfig, content_type="text/plain")




def main():
    labDetails = readYaml(labACCESS)
    allHostsIP, allHostsName = readAtdTopo(labDetails["topology"])
    restarted = 0
    uploadSwitchDetails(allHostsName,allHostsIP,labDetails)
    print("Successfully Uploaded")


main()