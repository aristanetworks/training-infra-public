#!/usr/bin/env python
import jsonrpclib
import ssl
import yaml
from cloud_logging_utils import setup_cloud_logging, log_operation_start, log_operation_success

# Initialize cloud logging
logger = setup_cloud_logging('reset-cvp')

#static files
labACCESS = '/etc/atd/ACCESS_INFO.yaml'
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context
def resetLab(allHosts,labPassword):
    user = 'arista'
    log_operation_start(logger, 'cvp-reset', host_count=len(allHosts))
    successful_resets = 0
    failed_resets = 0

    for IPaddress in allHosts:
        logger.info(f"Resetting CVP config on {IPaddress}", extra={'labels': {'ip': IPaddress, 'operation': 'cvp-reset'}})
        switch = jsonrpclib.Server("https://arista:{password}@{ipaddress}/command-api".format(password = labPassword, ipaddress = IPaddress))
        try:
            config = switch.runCmds(1,["enable",
                                       "configure",
                                       "aaa authentication login default group atds local",
                                       "ip host apiserver.arista.io 35.192.157.156",
                                       "ip host arista.io 34.67.65.165",
                                       "ip host www.arista.io 34.67.65.165",
                                       "ip host www.cv-staging.corp.arista.io 34.82.61.12",
                                       "ip name-server vrf MGMT 8.8.8.8",
                                       "daemon TerminAttr",
                                       "exec /usr/bin/TerminAttr -cvcompression=gzip-smashexcludes=ale,flexCounter,hardware,kni,pulse,strata,flowtracking/hardware -ingestexclude=/Sysdb/cell/1/agent,/Sysdb/cell/2/agent -cvaddr=192.168.0.5:9910 -cvauth=token,/tmp/token -cvvrf=MGMT -taillogs -disableaaa",
                                       "no shutdown",
                                       "aaa authorization exec default group atds local",
                                       "aaa authorization commands all default local",
                                       "write memory"
                                       ],"text")
            runConfig = (config[1]["output"])
            logger.info(f"Successfully reset CVP config on {IPaddress}", extra={'labels': {'ip': IPaddress, 'status': 'success'}})
            successful_resets += 1
        except Exception as e:
            error_msg = str(e)
            print(error_msg)
            print(f"Check eAPI is enabled on {IPaddress}")
            logger.error(f"Failed to reset CVP config on {IPaddress}: {error_msg}", extra={'labels': {'ip': IPaddress, 'error': error_msg}})
            failed_resets += 1

    log_operation_success(logger, 'cvp-reset', successful=successful_resets, failed=failed_resets)
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
    logger.info("Starting CVP reset process")
    labPassword, labTopology = readLabDetails()
    logger.info(f"Loaded lab topology: {labTopology}", extra={'labels': {'topology': labTopology}})

    allHosts = readAtdTopo(labTopology)
    logger.info(f"Found {len(allHosts)} hosts to reset", extra={'labels': {'host_count': str(len(allHosts))}})

    resetLab(allHosts,labPassword)
    logger.info("CVP reset process completed")
main()