#!/usr/bin/env python

#This script is designed to export the running configurations of each switch.
#It uses eAPI to connect to each switch and retrieve its configuration.
#The script reads a YAML file that contains login information for the lab, including the lab password and topology in use.
#It then reads another YAML file that contains a list of all IP addresses in the topology.
#The script prompts the user to select whether they are doing P-1&2 (P1) or P3 (P2).
#For each switch in the topology, it checks if it is listed in a dictionary called "switch_data". If it is, and if the user selected P1 or P2 as appropriate, it connects to the switch using eAPI and retrieves and sets its configuration.


import jsonrpclib
import yaml
import ssl
import requests



switch_data ={
    "leaf1-DC1": "both",
    "leaf2-DC1": "both",
    "leaf3-DC1": "both",
    "leaf4-DC1": "both",
    "spine1-DC1": "both",
    "spine2-DC1": "both",
    "spine3-DC1": "both",
    "leaf1-DC2": "p3",
    "leaf2-DC2": "p3",
    "leaf3-DC2": "p3",
    "leaf4-DC2": "p3",
    "spine1-DC2": "p3",
    "spine2-DC2": "p3",
    "spine3-DC2": "p3",

}

try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

requests.packages.urllib3.disable_warnings()

labACCESS = '/etc/atd/ACCESS_INFO.yaml'
regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'


def readLabDetails():
    # get the lab password and the topolgy in use
    with open(labACCESS) as f:
        labDetails = yaml.load(f,Loader=yaml.FullLoader)
    return labDetails['login_info']['jump_host']['pw'], labDetails['topology'],labDetails['name'],labDetails['zone']



def readAtdTopo(labTopology):
    #get a list of all IP addresses in the topology
    with open("/opt/atd/topologies/"+ labTopology +"/topo_build.yml") as f:
        topology = yaml.load(f,Loader=yaml.FullLoader)
        hostsIP = []
        hostsName = []
        for node in topology['nodes']:
            for key, value in node.items():
                hostsIP.append(value['ip_addr'])
                hostsName.append(key)

    return hostsIP[::-1],hostsName[::-1]


def grabSwitchDetails(allHostsName,allHostsIP,labPassword):
    print("This script will attempt fix the 'noif' issue within the L7 exam.  The script will remove the loopback0 interface and re-add it back with the same IP address. It will NOT create the loopback for you if you haven't already done it, it is only designed to save you time.  It does not take into account ANY configuration other than an IP address, so please check your loopback interfaces once you have run this script.")
    whatSection = input("""
    Which L7 parts are you doing?
    1) Parts 1 and 2
    2) Part 3
    """)
    for name, ip in zip(reversed(allHostsName),reversed(allHostsIP)):
        if name in switch_data and switch_data[name] == "both" and whatSection == "1":
            switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = ip))
            try:
                data = switch.runCmds(1,["enable", "show ip interface brief"],"json")
                loopback_ip = data[1]
                if "Loopback0"  in loopback_ip['interfaces']:
                    disable = switch.runCmds(1,["enable", "configure", "no interface loopback 0"],"text")
                    enable = switch.runCmds(1,["enable", "configure", "interface loopback 0", "ip address "+ str(loopback_ip['interfaces']['Loopback0']['interfaceAddress']['ipAddr']['address']) +"/24"])
                    print("Fixed " +name+ ". " + "Used IP address - " + str(loopback_ip['interfaces']['Loopback0']['interfaceAddress']['ipAddr']['address']) + "/24")
                else:
                    print("lo0 NOT configured, skipping " + name)
            except Exception as e:
                print(str(e))
                print("Check eAPI is enabled on {switch}".format(switch = name))
        elif name in switch_data and whatSection == "2":
                switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = ip))
                try:
                    data = switch.runCmds(1,["enable", "show ip interface brief"],"json")
                    loopback_ip = data[1]
                    disable = switch.runCmds(1,["enable", "configure", "no interface loopback 0"],"text")
                    enable = switch.runCmds(1,["enable", "configure", "interface loopback 0", "ip address "+ str(loopback_ip['interfaces']['Loopback0']['interfaceAddress']['ipAddr']['address']) +"/24"])
                    print("Fixed " +name)
                except Exception as e:
                    print(str(e))
                    print("Check eAPI is enabled on {switch}".format(switch = name))



def main():

    labPassword, labTopology, labName, labZone = readLabDetails()
    allHostsIP, allHostsName = readAtdTopo(labTopology)
    grabSwitchDetails(allHostsName,allHostsIP,labPassword)

main()