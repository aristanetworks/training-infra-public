#!/usr/bin/env python

import libvirt
import time
import jsonrpclib
import yaml
import ssl
import sys
from cloud_logging_utils import setup_cloud_logging, log_operation_start, log_operation_success

# Initialize cloud logging
logger = setup_cloud_logging('resetVMs')

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
    logger.info("Starting VM reset process")
    log_operation_start(logger, 'reset-vms')

    labPassword, labTopology = readLabDetails()
    logger.info(f"Loaded lab topology: {labTopology}", extra={'labels': {'topology': labTopology}})

    allHostsIP, allHostsName = readAtdTopo(labTopology)
    logger.info(f"Found {len(allHostsName)} hosts to check", extra={'labels': {'host_count': str(len(allHostsName))}})

    restarted = 0
    for name, ip in zip(allHostsName,allHostsIP):
        logger.info(f"Checking {name} ({ip})", extra={'labels': {'device': name, 'ip': ip}})

        switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = ip))
        try:
            switch.runCmds(1,["show version"])
        except:
            print("Switch {switch} appears to have no eAPI connectivity".format(switch = name))
            logger.warning(f"Switch {name} has no eAPI connectivity", extra={'labels': {'device': name, 'status': 'no_eapi'}})

            machine_to_kill = _get_libvirt_machine(name)
            print("Restarting {switch}".format(switch = name))
            logger.info(f"Restarting VM {name}", extra={'labels': {'device': name, 'action': 'restart'}})

            try:
                machine_to_kill.destroy()
            except:
                print("Switch does not exists.")
                logger.error(f"VM {name} does not exist", extra={'labels': {'device': name, 'error': 'vm_not_found'}})

            time.sleep(3)
            machine_to_kill.create()
            print("Restarted {switch}".format(switch = name))
            logger.info(f"Successfully restarted VM {name}", extra={'labels': {'device': name, 'status': 'restarted'}})
            restarted += 1
        else:
            print("Switch {switch} seems ok".format(switch = name))
            logger.info(f"Switch {name} is OK", extra={'labels': {'device': name, 'status': 'ok'}})

    if restarted >=1:
        print("Switches were restarted, please wait for 5 minutes before running again")
        log_operation_success(logger, 'reset-vms', restarted=restarted)
    else:
        print("No problems were detected, please check with your instructor")
        logger.info("No VMs needed restart", extra={'labels': {'restarted': 0}})


main()