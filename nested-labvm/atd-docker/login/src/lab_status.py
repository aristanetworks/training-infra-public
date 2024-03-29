#!/usr/bin/env python

import libvirt
import time
import jsonrpclib
import yaml
import ssl
import sys

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


def _get_libvirt_machine(machine):
    #libvirt.registerErrorHandler(f=_libvirt_silence_error, ctx=None)
    try:
        conn = libvirt.open("qemu:///system")
    except:
        print("Unable to connect to local HV. Are you using sudo?")
        sys.exit()
    else:
        libvirt_machine = conn.lookupByName(machine)
        return libvirt_machine


def main():
    labPassword, labTopology = readLabDetails()
    allHostsIP, allHostsName = readAtdTopo(labTopology)
    switch_status=[]
    for name, ip in zip(allHostsName,allHostsIP):
        switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = ip))
        try:
            switch.runCmds(1,["show version"])
        except:
            print("{switch},Down".format(switch = name))
        else:
            print("{switch},Ok".format(switch = name))
main()