#!/usr/bin/env python

#Save running config on all switches

import jsonrpclib,ssl,sys
import yaml
from cloud_logging_utils import setup_cloud_logging, log_operation_start, log_operation_success

logger = setup_cloud_logging('saveRunning')

#static files
labACCESS = '/etc/atd/ACCESS_INFO.yaml'



try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

def saveRunningConfig(allHosts,labPassword):
    log_operation_start(logger, 'save-running', host_count=len(allHosts))
    successful = 0
    failed = 0

    for IPaddress in allHosts:
        try:
            #use eAPI to copy the running-config to start-config
            switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = IPaddress))
            switch.runCmds( 1, [ "enable", "copy running-config startup-config" ] )
            print("Done {0}".format(IPaddress))
            logger.info(f"Saved running config on {IPaddress}", extra={'labels': {'ip': IPaddress, 'status': 'saved'}})
            successful += 1

        except KeyboardInterrupt:
            print("Caught Keyboard Interrupt - Exiting")
            logger.warning("Save running config interrupted by user")
            sys.exit()

        except OSError as ERR :
            # Socket Errors
            print(ERR)
            logger.error(f"Socket error on {IPaddress}: {ERR}", extra={'labels': {'ip': IPaddress, 'error': str(ERR)}})
            failed += 1

    log_operation_success(logger, 'save-running', successful=successful, failed=failed)



def readLabDetails():
    # get the lab password and the topolgy in use
    with open(labACCESS) as f:
        labDetails = yaml.load(f,Loader=yaml.FullLoader)
    return labDetails['login_info']['jump_host']['pw'], labDetails['topology']



def readAtdTopo(labTopology):
    #get a list of all IP addresses in the topology
    with open("/opt/atd/topologies/"+ labTopology +"/topo_build.yml") as f:
       topology = yaml.load(f,Loader=yaml.FullLoader)
    hosts = []
    for a in topology['nodes']:
        for key in a.keys():
            hosts.append(a[key]['ip_addr'])
    return hosts


def main():
    logger.info("Starting save running config process")
    labPassword, labTopology = readLabDetails()
    logger.info(f"Loaded topology: {labTopology}", extra={'labels': {'topology': labTopology}})

    allHosts = readAtdTopo(labTopology)
    logger.info(f"Found {len(allHosts)} hosts", extra={'labels': {'host_count': str(len(allHosts))}})

    saveRunningConfig(allHosts,labPassword)
    logger.info("Save running config process completed")

main()

